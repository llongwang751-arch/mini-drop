// Mini-Drop collection control plane.
//
// This gRPC service is the rendezvous point between the Go API and remote C++
// Agents. The API enqueues durable task intent; Agents register, heartbeat and
// pull work; result callbacks advance the persisted collection state. Large
// artifacts bypass gRPC and travel through object storage.
#include <grpcpp/grpcpp.h>
#include <grpcpp/health_check_service_interface.h>

#include "healthcheck.grpc.pb.h"
#include "hotmethod.grpc.pb.h"
#include "init.grpc.pb.h"
#include "control.grpc.pb.h"
#include "taskkind_contract.h"
#include "status_contract.h"
#include "error_code_contract.h"

#include <google/protobuf/empty.pb.h>
#include <nlohmann/json.hpp>
#include <openssl/crypto.h>
#include <openssl/rand.h>
#include <openssl/sha.h>
#include <pqxx/pqxx>

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

using json = nlohmann::json;

namespace {

std::string env_or(const char* name, const std::string& fallback) {
  const char* value = std::getenv(name);
  return value && *value ? value : fallback;
}

bool env_bool(const char* name, bool fallback = false) {
  std::string value = env_or(name, fallback ? "1" : "0");
  std::transform(value.begin(), value.end(), value.begin(), ::tolower);
  return value == "1" || value == "true" || value == "yes" || value == "on";
}

int env_int(const char* name, int fallback) {
  try {
    return std::max(1, std::stoi(env_or(name, std::to_string(fallback))));
  } catch (...) {
    return fallback;
  }
}

std::string normalize_database_url(std::string value) {
  const std::string sqlalchemy = "postgresql+psycopg://";
  if (value.rfind(sqlalchemy, 0) == 0) {
    value.replace(0, sqlalchemy.size(), "postgresql://");
  }
  return value;
}

std::string random_id(const std::string& prefix) {
  static thread_local std::mt19937_64 random{std::random_device{}()};
  std::ostringstream out;
  out << prefix << std::hex << std::setw(16) << std::setfill('0') << random();
  return out.str();
}

std::string hex_bytes(const unsigned char* input, std::size_t size) {
  constexpr char digits[] = "0123456789abcdef";
  std::string output(size * 2, '0');
  for (std::size_t index = 0; index < size; ++index) {
    output[index * 2] = digits[(input[index] >> 4) & 0x0f];
    output[index * 2 + 1] = digits[input[index] & 0x0f];
  }
  return output;
}

std::string random_task_attempt_authority() {
  std::array<unsigned char, 32> bytes{};
  if (RAND_bytes(bytes.data(), static_cast<int>(bytes.size())) != 1) {
    throw std::runtime_error("secure task-attempt authority generation failed");
  }
  return hex_bytes(bytes.data(), bytes.size());
}

std::string sha256_hex(const std::string& input) {
  std::array<unsigned char, SHA256_DIGEST_LENGTH> digest{};
  SHA256(reinterpret_cast<const unsigned char*>(input.data()), input.size(),
         digest.data());
  return hex_bytes(digest.data(), digest.size());
}

bool secure_equal(const std::string& left, const std::string& right) {
  return left.size() == right.size() && !left.empty() &&
      CRYPTO_memcmp(left.data(), right.data(), left.size()) == 0;
}

struct Config {
  std::string listen_addr;
  std::string database_url;
  std::string grpc_token;
  bool auth_enabled;
  bool grpc_secure;
  std::string grpc_cert_file;
  std::string grpc_key_file;
  std::string grpc_ca_file;
  std::string grpc_client_cert_file;
  std::string grpc_client_key_file;
  bool grpc_require_client_cert;
  std::string grpc_api_client_identity;
  std::string minio_bucket;
  int agent_offline_timeout_sec;
  int maintenance_interval_sec;
  int process_snapshot_retention;
  int process_snapshot_max_age_sec;
};

Config load_config() {
  Config config{
      env_or("NATIVE_CONTROL_LISTEN_ADDR", "0.0.0.0:50051"),
      normalize_database_url(env_or(
          "DATABASE_URL", "postgresql://mini_drop:mini_drop@postgres:5432/mini_drop")),
      env_or("MINI_DROP_GRPC_TOKEN", ""),
      env_bool("MINI_DROP_GRPC_AUTH_ENABLED"),
      env_bool("MINI_DROP_GRPC_SECURE"),
      env_or("MINI_DROP_GRPC_CERT_FILE", ""),
      env_or("MINI_DROP_GRPC_KEY_FILE", ""),
      env_or("MINI_DROP_GRPC_CA_FILE", ""),
      env_or("MINI_DROP_GRPC_CLIENT_CERT_FILE", ""),
      env_or("MINI_DROP_GRPC_CLIENT_KEY_FILE", ""),
      env_bool("MINI_DROP_GRPC_REQUIRE_CLIENT_CERT"),
      env_or("MINI_DROP_GRPC_API_CLIENT_IDENTITY", "mini-drop-control-client"),
      env_or("MINIO_BUCKET", "mini-drop"),
      env_int("AGENT_OFFLINE_TIMEOUT_SEC", 30),
      env_int("MINI_DROP_CONTROL_MAINTENANCE_SEC", 5),
      std::min(10000, std::max(2, env_int(
          "MINI_DROP_PROCESS_SNAPSHOT_RETENTION_PER_AGENT", 120))),
      env_int("MINI_DROP_PROCESS_SNAPSHOT_MAX_AGE_SEC", 30)};
  std::string environment = env_or("MINI_DROP_ENV", "dev");
  std::transform(environment.begin(), environment.end(), environment.begin(), ::tolower);
  if (environment == "production") {
    if (!config.auth_enabled || config.grpc_token.empty()) {
      throw std::runtime_error(
          "production requires non-empty gRPC token authentication");
    }
    if (!config.grpc_secure || !config.grpc_require_client_cert ||
        config.grpc_ca_file.empty()) {
      throw std::runtime_error(
          "production requires gRPC mTLS with verified client certificates");
    }
    if (config.grpc_api_client_identity.empty()) {
      throw std::runtime_error(
          "production requires MINI_DROP_GRPC_API_CLIENT_IDENTITY");
    }
  }
  return config;
}

std::string bounded_text(std::string value, std::size_t max_length = 1024) {
  value.erase(std::remove(value.begin(), value.end(), '\0'), value.end());
  if (value.size() > max_length) value.resize(max_length);
  return value;
}

bool valid_traceparent(const std::string& value) {
  if (value.size() != 55 || value.substr(0, 3) != "00-" ||
      value[35] != '-' || value[52] != '-') {
    return false;
  }
  for (std::size_t index = 0; index < value.size(); ++index) {
    if (index == 2 || index == 35 || index == 52) continue;
    if (!std::isxdigit(static_cast<unsigned char>(value[index])) ||
        std::isupper(static_cast<unsigned char>(value[index]))) {
      return false;
    }
  }
  return value.substr(3, 32) != std::string(32, '0') &&
      value.substr(36, 16) != std::string(16, '0');
}

std::int64_t bounded_u64(std::uint64_t value) {
  const auto maximum = static_cast<std::uint64_t>(
      std::numeric_limits<std::int64_t>::max());
  return static_cast<std::int64_t>(std::min(value, maximum));
}

void persist_process_snapshot(
    pqxx::work& tx,
    const Config& config,
    const std::string& agent_id,
    const mini_drop::ProcessCandidateSnapshot& snapshot) {
  constexpr int kMaxCandidates = 256;
  constexpr int kMaxCapabilities = 16;

  bool invalid = snapshot.candidates_size() > kMaxCandidates;
  const bool truncated = snapshot.truncated() || invalid;
  const std::string boot_id = bounded_text(snapshot.boot_id());
  const std::string error = bounded_text(snapshot.error());
  if (snapshot.generation() >
          static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()) ||
      snapshot.observed_at_unix_ms() >
          static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max())) {
    invalid = true;
  }

  struct CandidateRow {
    int pid;
    std::int64_t process_start_ticks;
    std::int64_t pid_namespace_inode;
    int namespace_pid;
    std::string executable_identity;
    std::string comm;
    std::string cgroup;
    std::string service_hint;
    std::string instance_hint;
    std::string collector_capabilities;
  };
  std::vector<CandidateRow> candidates;
  candidates.reserve(std::min(snapshot.candidates_size(), kMaxCandidates));
  std::unordered_set<std::string> identities;
  for (int index = 0;
       index < snapshot.candidates_size() && index < kMaxCandidates;
       ++index) {
    const auto& candidate = snapshot.candidates(index);
    const std::string executable_identity =
        bounded_text(candidate.executable_identity());
    const bool candidate_valid =
        candidate.pid() > 0 &&
        candidate.pid() <= static_cast<std::uint32_t>(
            std::numeric_limits<int>::max()) &&
        candidate.process_start_ticks() > 0 &&
        candidate.process_start_ticks() <= static_cast<std::uint64_t>(
            std::numeric_limits<std::int64_t>::max()) &&
        candidate.pid_namespace_inode() > 0 &&
        candidate.pid_namespace_inode() <= static_cast<std::uint64_t>(
            std::numeric_limits<std::int64_t>::max()) &&
        candidate.namespace_pid() > 0 &&
        candidate.namespace_pid() <= static_cast<std::uint32_t>(
            std::numeric_limits<int>::max()) &&
        !executable_identity.empty();
    if (!candidate_valid) {
      invalid = true;
      continue;
    }

    std::ostringstream identity;
    identity << candidate.pid() << ':' << candidate.process_start_ticks()
             << ':' << candidate.pid_namespace_inode() << ':'
             << candidate.namespace_pid() << ':' << executable_identity;
    if (!identities.insert(identity.str()).second) {
      invalid = true;
      continue;
    }

    json capabilities = json::array();
    if (candidate.collector_capabilities_size() > kMaxCapabilities) {
      invalid = true;
    }
    for (int capability_index = 0;
         capability_index < candidate.collector_capabilities_size() &&
         capability_index < kMaxCapabilities;
         ++capability_index) {
      capabilities.push_back(bounded_text(
          candidate.collector_capabilities(capability_index), 64));
    }
    candidates.push_back(CandidateRow{
        static_cast<int>(candidate.pid()),
        bounded_u64(candidate.process_start_ticks()),
        bounded_u64(candidate.pid_namespace_inode()),
        static_cast<int>(candidate.namespace_pid()),
        executable_identity,
        bounded_text(candidate.comm()),
        bounded_text(candidate.cgroup()),
        bounded_text(candidate.service_hint()),
        bounded_text(candidate.instance_hint()),
        capabilities.dump()});
  }

  std::string state;
  if (!error.empty()) {
    state = "failed";
  } else if (truncated) {
    state = "truncated";
  } else if (!snapshot.complete() || invalid || snapshot.generation() == 0 ||
             boot_id.empty()) {
    state = "partial";
  } else if (candidates.empty()) {
    state = "complete-empty";
  } else {
    state = "complete-populated";
  }
  const bool authoritative =
      state == "complete-empty" || state == "complete-populated";
  const std::string snapshot_id = random_id("psnap_");
  tx.exec_params(
      "INSERT INTO process_candidate_snapshots("
      "id,agent_id,generation,boot_id,observed_at_unix_ms,complete,truncated,"
      "error,state,authoritative,received_at) "
      "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,now())",
      snapshot_id, agent_id, bounded_u64(snapshot.generation()), boot_id,
      bounded_u64(snapshot.observed_at_unix_ms()), snapshot.complete(),
      truncated, error, state, authoritative);
  for (const auto& candidate : candidates) {
    tx.exec_params(
        "INSERT INTO process_candidates("
        "snapshot_id,agent_id,pid,process_start_ticks,pid_namespace_inode,"
        "namespace_pid,executable_identity,comm,cgroup,service_hint,"
        "instance_hint,collector_capabilities) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::json)",
        snapshot_id, agent_id, candidate.pid, candidate.process_start_ticks,
        candidate.pid_namespace_inode, candidate.namespace_pid,
        candidate.executable_identity, candidate.comm, candidate.cgroup,
        candidate.service_hint, candidate.instance_hint,
        candidate.collector_capabilities);
  }

  tx.exec_params(
      "DELETE FROM process_candidate_snapshots s WHERE s.id IN ("
      "SELECT id FROM process_candidate_snapshots WHERE agent_id=$1 "
      "ORDER BY received_at DESC,id DESC OFFSET $2 LIMIT 100) "
      "AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.process_snapshot_id=s.id) "
      "AND NOT EXISTS (SELECT 1 FROM drop_insight_target_bindings b "
      "WHERE b.process_snapshot_id=s.id)",
      agent_id, config.process_snapshot_retention);
}

bool process_binding_matches_latest(
    pqxx::work& tx,
    const Config& config,
    const std::string& agent_id,
    int target_pid,
    const std::string& binding_json) {
  try {
    const json binding = json::parse(binding_json);
    if (binding.value("agent_id", std::string{}) != agent_id ||
        binding.value("pid", 0) != target_pid) {
      return false;
    }
    const auto matches = tx.exec_params(
        "WITH latest AS ("
        "SELECT id,agent_id,boot_id,authoritative,received_at "
        "FROM process_candidate_snapshots WHERE agent_id=$1 "
        "ORDER BY received_at DESC,id DESC LIMIT 1) "
        "SELECT count(*) FROM latest l JOIN process_candidates p "
        "ON p.snapshot_id=l.id WHERE l.authoritative=true "
        "AND l.received_at >= now()-($9::int * interval '1 second') "
        "AND p.pid=$2 AND l.boot_id=$3 AND p.process_start_ticks=$4 "
        "AND p.pid_namespace_inode=$5 AND p.namespace_pid=$6 "
        "AND p.executable_identity=$7 AND l.agent_id=$8",
        agent_id, target_pid, binding.at("boot_id").get<std::string>(),
        binding.at("process_start_ticks").get<std::int64_t>(),
        binding.at("pid_namespace_inode").get<std::int64_t>(),
        binding.at("namespace_pid").get<int>(),
        binding.at("executable_identity").get<std::string>(),
        binding.at("agent_id").get<std::string>(),
        config.process_snapshot_max_age_sec);
    return !matches.empty() && matches[0][0].as<int>() == 1;
  } catch (...) {
    return false;
  }
}

std::string read_file(const std::string& path) {
  if (path.empty()) return "";
  std::ifstream input(path, std::ios::binary);
  if (!input) throw std::runtime_error("cannot read TLS file: " + path);
  std::ostringstream content;
  content << input.rdbuf();
  return content.str();
}

std::shared_ptr<grpc::ServerCredentials> server_credentials(const Config& config) {
  if (!config.grpc_secure) return grpc::InsecureServerCredentials();
  if (config.grpc_cert_file.empty() || config.grpc_key_file.empty()) {
    throw std::runtime_error(
        "MINI_DROP_GRPC_CERT_FILE and MINI_DROP_GRPC_KEY_FILE are required");
  }
  grpc::SslServerCredentialsOptions options;
  options.pem_key_cert_pairs.push_back({
      read_file(config.grpc_key_file), read_file(config.grpc_cert_file)});
  if (config.grpc_require_client_cert) {
    if (config.grpc_ca_file.empty()) {
      throw std::runtime_error(
          "MINI_DROP_GRPC_CA_FILE is required when client certificates are required");
    }
    options.pem_root_certs = read_file(config.grpc_ca_file);
    options.client_certificate_request =
        GRPC_SSL_REQUEST_AND_REQUIRE_CLIENT_CERTIFICATE_AND_VERIFY;
  }
  return grpc::SslServerCredentials(options);
}

std::shared_ptr<grpc::Channel> health_channel(const Config& config) {
  const std::string address =
      env_or("NATIVE_CONTROL_HEALTH_ADDR", "127.0.0.1:50051");
  if (!config.grpc_secure) {
    return grpc::CreateChannel(address, grpc::InsecureChannelCredentials());
  }
  grpc::SslCredentialsOptions options;
  options.pem_root_certs = read_file(config.grpc_ca_file);
  if (!config.grpc_client_cert_file.empty() && !config.grpc_client_key_file.empty()) {
    options.pem_cert_chain = read_file(config.grpc_client_cert_file);
    options.pem_private_key = read_file(config.grpc_client_key_file);
  } else if (config.grpc_require_client_cert) {
    throw std::runtime_error(
        "control healthcheck requires MINI_DROP_GRPC_CLIENT_CERT_FILE and key");
  }
  grpc::ChannelArguments arguments;
  const std::string server_name =
      env_or("NATIVE_CONTROL_HEALTH_TLS_SERVER_NAME", "");
  if (!server_name.empty()) arguments.SetSslTargetNameOverride(server_name);
  return grpc::CreateCustomChannel(
      address, grpc::SslCredentials(options), arguments);
}

void run_maintenance(const Config config) {
  while (true) {
    try {
      pqxx::connection connection(config.database_url);
      pqxx::work tx(connection);
      const auto stale = tx.exec_params(
          "UPDATE agents SET status='OFFLINE',updated_at=now() "
          "WHERE status='ONLINE' AND last_heartbeat_at < "
          "now() - ($1::int * interval '1 second') RETURNING id",
          config.agent_offline_timeout_sec);
      for (const auto& row : stale) {
        const std::string agent_id = row[0].as<std::string>();
        tx.exec_params(
            "INSERT INTO audit_logs(event_type,message,agent_id,metadata,created_at) "
            "VALUES('AGENT_OFFLINE',$1,$2,$3::jsonb,now())",
            agent_id + " heartbeat timed out", agent_id,
            R"({"served_by":"cpp-control","reason":"heartbeat_timeout"})");
      }
      tx.commit();
      if (!stale.empty()) {
        std::cout << R"({"level":"info","event":"agents_marked_offline","count":)"
                  << stale.size() << "}" << std::endl;
      }
    } catch (const std::exception& error) {
      std::cerr << R"({"level":"error","event":"control_maintenance_failed","error":")"
                << error.what() << R"("})" << std::endl;
    }
    std::this_thread::sleep_for(
        std::chrono::seconds(config.maintenance_interval_sec));
  }
}

class ServiceBase {
 public:
  explicit ServiceBase(const Config& config) : config_(config) {}

 protected:
  grpc::Status authorize(grpc::ServerContext* context) const {
    if (!config_.auth_enabled) return grpc::Status::OK;
    const auto values = context->client_metadata().find("x-mini-drop-grpc-token");
    if (values == context->client_metadata().end() ||
        std::string(values->second.data(), values->second.length()) != config_.grpc_token) {
      return grpc::Status(grpc::StatusCode::UNAUTHENTICATED, "invalid gRPC token");
    }
    return grpc::Status::OK;
  }

  bool peer_identity_matches(
      grpc::ServerContext* context, const std::string& expected) const {
    if (!config_.grpc_require_client_cert) return true;
    const auto auth = context->auth_context();
    if (!auth || !auth->IsPeerAuthenticated()) return false;
    for (const auto& identity : auth->GetPeerIdentity()) {
      if (std::string(identity.data(), identity.length()) == expected) {
        return true;
      }
    }
    return false;
  }

  grpc::Status authorize_agent(
      grpc::ServerContext* context, const std::string& agent_id) const {
    if (const auto status = authorize(context); !status.ok()) return status;
    if (agent_id.empty() || !peer_identity_matches(context, agent_id)) {
      return grpc::Status(
          grpc::StatusCode::PERMISSION_DENIED,
          "mTLS identity is not authorized for this agent_id");
    }
    return grpc::Status::OK;
  }

  grpc::Status authorize_control(grpc::ServerContext* context) const {
    if (const auto status = authorize(context); !status.ok()) return status;
    if (!peer_identity_matches(context, config_.grpc_api_client_identity)) {
      return grpc::Status(
          grpc::StatusCode::PERMISSION_DENIED,
          "mTLS identity is not authorized for Control service");
    }
    return grpc::Status::OK;
  }

  pqxx::connection database() const { return pqxx::connection(config_.database_url); }
  const Config& config_;
};

class InitService final : public mini_drop::InitAgent::Service, private ServiceBase {
 public:
  explicit InitService(const Config& config) : ServiceBase(config) {}

  grpc::Status RegisterAgent(
      grpc::ServerContext* context,
      const mini_drop::RegisterAgentRequest* request,
      mini_drop::RegisterAgentResponse* response) override {
    if (const auto status = authorize_agent(context, request->agent_id()); !status.ok()) return status;
    try {
      auto connection = database();
      pqxx::work tx(connection);
      const auto previous = tx.exec_params(
          "SELECT status FROM agents WHERE id=$1 FOR UPDATE", request->agent_id());
      json capabilities = json::array();
      for (const auto& item : request->capabilities()) capabilities.push_back(item);
      tx.exec_params(
          "INSERT INTO agents(id,hostname,ip_addr,version,os_info,capabilities,status,"
          "last_heartbeat_at,created_at,updated_at) "
          "VALUES($1,$2,$3,$4,$5,$6::jsonb,'ONLINE',now(),now(),now()) "
          "ON CONFLICT(id) DO UPDATE SET hostname=excluded.hostname,ip_addr=excluded.ip_addr,"
          "version=excluded.version,os_info=excluded.os_info,capabilities=excluded.capabilities,"
          "status='ONLINE',last_heartbeat_at=now(),updated_at=now()",
          request->agent_id(), request->hostname(), request->ip_addr(), request->version(),
          request->os_info(), capabilities.dump());
      if (!previous.empty() && previous[0][0].as<std::string>() == "OFFLINE") {
        tx.exec_params(
            "INSERT INTO audit_logs(event_type,message,agent_id,metadata,created_at) "
            "VALUES('AGENT_ONLINE',$1,$2,$3::jsonb,now())",
            request->agent_id() + " 恢复在线", request->agent_id(),
            R"({"served_by":"cpp-control"})");
      }
      tx.commit();
      response->set_heartbeat_interval_sec(5);
      return grpc::Status::OK;
    } catch (const std::exception& error) {
      return grpc::Status(grpc::StatusCode::INTERNAL, error.what());
    }
  }

  grpc::Status FetchConfig(
      grpc::ServerContext* context,
      const mini_drop::FetchConfigRequest* request,
      mini_drop::FetchConfigResponse* response) override {
    if (const auto status = authorize_agent(context, request->agent_id()); !status.ok()) return status;
    auto* cos = response->mutable_cos_config();
    cos->set_bucket(config_.minio_bucket);
    return grpc::Status::OK;
  }
};

int profiler_type(const std::string& collector) {
  const auto* contract = mini_drop_contract::find_by_name(collector);
  if (contract == nullptr) throw std::invalid_argument("unknown TaskKind: " + collector);
  return contract->profiler_type;
}

class ControlService final : public mini_drop::Control::Service, private ServiceBase {
 public:
  explicit ControlService(const Config& config) : ServiceBase(config) {}

  grpc::Status CreateTask(
      grpc::ServerContext* context,
      const mini_drop::CreateTaskRequest* request,
      mini_drop::CreateTaskResponse* response) override {
    if (const auto status = authorize_control(context); !status.ok()) return status;
    if (request->task_id().empty() || request->target_ip().empty() ||
        !request->has_task_desc() || request->task_desc().task_id() != request->task_id()) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                          "task_id, target and consistent TaskDesc are required");
    }
    try {
      auto connection = database();
      pqxx::work tx(connection);
      const auto rows = tx.exec_params(
          "SELECT t.status,a.status FROM tasks t JOIN agents a ON a.id=t.agent_id "
          "WHERE t.id=$1 AND (a.id=$2 OR a.ip_addr=$2) FOR UPDATE OF t",
          request->task_id(), request->target_ip());
      if (rows.empty()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND,
                            "persisted task or target agent not found");
      }
      const std::string task_status = rows[0][0].as<std::string>();
      const std::string agent_status = rows[0][1].as<std::string>();
      if (agent_status != "ONLINE") {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "target agent is not online");
      }
      if (task_status != "PENDING" && task_status != "RUNNING") {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "task is not dispatchable");
      }
      tx.exec_params(
          "UPDATE tasks SET status_reason=$2,updated_at=now() WHERE id=$1",
          request->task_id(), "C++ Control.CreateTask 已确认调度");
      const auto request_id_header =
          context->client_metadata().find("x-request-id");
      const std::string request_id =
          request_id_header == context->client_metadata().end()
              ? ""
              : std::string(request_id_header->second.data(),
                            request_id_header->second.length());
      const auto traceparent_header =
          context->client_metadata().find("traceparent");
      std::string traceparent =
          traceparent_header == context->client_metadata().end()
              ? ""
              : bounded_text(
                    std::string(traceparent_header->second.data(),
                                traceparent_header->second.length()),
                    55);
      if (!valid_traceparent(traceparent)) traceparent.clear();
      const json metadata = {
          {"served_by", "cpp-control"}, {"request_id", request_id},
          {"traceparent", traceparent},
          {"trace_id", traceparent.empty() ? "" : traceparent.substr(3, 32)}};
      tx.exec_params(
          "INSERT INTO audit_logs(event_type,message,task_id,metadata,created_at) "
          "VALUES('TASK_DISPATCH_CONFIRMED',$1,$2,$3::jsonb,now())",
          "C++ 控制面确认任务可调度", request->task_id(), metadata.dump());
      tx.commit();
      response->set_task_id(request->task_id());
      response->set_status(task_status);
      return grpc::Status::OK;
    } catch (const std::exception& error) {
      return grpc::Status(grpc::StatusCode::INTERNAL, error.what());
    }
  }

  grpc::Status StatAgent(
      grpc::ServerContext* context,
      const mini_drop::StatAgentRequest* request,
      mini_drop::StatAgentResponse* response) override {
    if (const auto status = authorize_control(context); !status.ok()) return status;
    if (request->agent_id().empty()) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "agent_id is required");
    }
    try {
      auto connection = database();
      pqxx::read_transaction tx(connection);
      const auto rows = tx.exec_params(
          "SELECT status FROM agents WHERE id=$1", request->agent_id());
      if (rows.empty()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND, "agent not found");
      }
      response->set_agent_status(rows[0][0].as<std::string>());
      return grpc::Status::OK;
    } catch (const std::exception& error) {
      return grpc::Status(grpc::StatusCode::INTERNAL, error.what());
    }
  }
};

class HealthService final : public mini_drop::HealthCheck::Service, private ServiceBase {
 public:
  explicit HealthService(const Config& config) : ServiceBase(config) {}

  grpc::Status Do(
      grpc::ServerContext* context,
      const mini_drop::HealthCheckRequest* request,
      mini_drop::HealthCheckResponse* response) override {
    if (const auto status = authorize_agent(context, request->agent_id()); !status.ok()) return status;
    response->set_status(mini_drop::HealthCheckResponse::SERVING);
    try {
      auto connection = database();
      pqxx::work tx(connection);
      tx.exec_params(
          "UPDATE agents SET ip_addr=CASE WHEN $2='' THEN ip_addr ELSE $2 END,status='ONLINE',"
          "last_heartbeat_at=now(),updated_at=now() WHERE id=$1",
          request->agent_id(), request->ip_addr());
      if (request->has_process_candidate_snapshot()) {
        persist_process_snapshot(
            tx, config_, request->agent_id(),
            request->process_candidate_snapshot());
      }

      if (request->busy()) {
        if (!request->active_task_id().empty()) {
          tx.exec_params(
              "UPDATE tasks SET collection_status=$2,updated_at=now() "
              "WHERE id=$1 AND status='RUNNING' AND collection_status=$3",
              request->active_task_id(),
              mini_drop_contract::kCollectionRunning.data(),
              mini_drop_contract::kCollectionDelivered.data());
          const auto rows = tx.exec_params(
              "SELECT status,status_reason FROM tasks WHERE id=$1",
              request->active_task_id());
          if (!rows.empty() && rows[0][0].as<std::string>() == "CANCELLED") {
            response->set_cancel_task_id(request->active_task_id());
            response->set_cancel_reason(rows[0][1].is_null()
                ? "任务已取消" : rows[0][1].as<std::string>());
          }
        }
        tx.commit();
        return grpc::Status::OK;
      }

      const auto tasks = tx.exec_params(
          "SELECT id,target_pid,collector_type,sample_rate,duration_sec,request_params,"
          "COALESCE(process_binding_json,'{}'::json)::text "
          "FROM tasks WHERE agent_id=$1 AND status='PENDING' "
          "ORDER BY created_at ASC FOR UPDATE SKIP LOCKED LIMIT 1",
          request->agent_id());
      if (tasks.empty()) {
        tx.commit();
        return grpc::Status::OK;
      }

      const auto row = tasks[0];
      const std::string task_id = row[0].as<std::string>();
      const std::string collector = row[2].as<std::string>();
      const auto* kind_contract = mini_drop_contract::find_by_name(collector);
      if (kind_contract == nullptr) {
        throw std::runtime_error("unknown TaskKind in persisted task: " + collector);
      }
      if (!process_binding_matches_latest(
              tx, config_, request->agent_id(), row[1].as<int>(),
              row[6].as<std::string>())) {
        tx.exec_params(
            "UPDATE tasks SET status='FAILED',status_reason=$2,"
            "collection_status='FAILED',analysis_status='CANCELED',"
            "error_code=$3,error_message=$2,"
            "finished_at=now(),updated_at=now() WHERE id=$1",
            task_id, "目标进程身份已变化或快照过期",
            std::string(mini_drop_contract::kErrorTargetIdentityChanged));
        tx.exec_params(
            "INSERT INTO task_status_events("
            "task_id,from_status,to_status,reason,actor,metadata,created_at) "
            "VALUES($1,'PENDING','FAILED',$2,'server',$3::jsonb,now())",
            task_id, "目标进程身份已变化或快照过期",
            (json{{"served_by", "cpp-control"}, {"error_code",
                std::string(mini_drop_contract::kErrorTargetIdentityChanged)}}).dump());
        tx.commit();
        return grpc::Status::OK;
      }

      const auto upload_authorizations = tx.exec_params(
          "SELECT object_key,put_url,"
          "(EXTRACT(EPOCH FROM expires_at)*1000)::bigint,task_attempt_id "
          "FROM task_upload_authorizations "
          "WHERE task_id=$1 AND used_at IS NULL AND expires_at>now() "
          "ORDER BY object_key ASC FOR UPDATE",
          task_id);
      if (static_cast<int>(upload_authorizations.size()) !=
          kind_contract->artifact_count) {
        tx.exec_params(
            "UPDATE tasks SET status='FAILED',status_reason=$2,"
            "collection_status='FAILED',analysis_status='CANCELED',"
            "error_code=$3,error_message=$2,"
            "finished_at=now(),updated_at=now() WHERE id=$1",
            task_id, "任务缺少完整、有效的短时对象上传授权",
            std::string(mini_drop_contract::kErrorUploadAuthorizationInvalid));
        tx.exec_params(
            "INSERT INTO task_status_events("
            "task_id,from_status,to_status,reason,actor,metadata,created_at) "
            "VALUES($1,'PENDING','FAILED',$2,'server',$3::jsonb,now())",
            task_id, "任务缺少完整、有效的短时对象上传授权",
            (json{{"served_by", "cpp-control"}, {"error_code",
                std::string(mini_drop_contract::kErrorUploadAuthorizationInvalid)}}).dump());
        tx.commit();
        return grpc::Status::OK;
      }
      const std::string attempt_id =
          upload_authorizations[0][3].as<std::string>();
      const std::string attempt_prefix =
          "tasks/" + task_id + "/attempts/" + attempt_id + "/";
      const std::string raw_object_prefix = attempt_prefix + "raw/";
      const std::string manifest_object_key = attempt_prefix + "manifest.json";
      const auto is_expected_object_key = [&](const std::string& object_key) {
        if (object_key == manifest_object_key) {
          return mini_drop_contract::is_artifact_filename(
              kind_contract->profiler_type, "manifest.json");
        }
        if (object_key.rfind(raw_object_prefix, 0) != 0) return false;
        const std::string filename = object_key.substr(raw_object_prefix.size());
        return filename.find('/') == std::string::npos &&
            filename.find('\\') == std::string::npos &&
            mini_drop_contract::is_artifact_filename(
                kind_contract->profiler_type, filename);
      };
      for (const auto& upload : upload_authorizations) {
        const std::string object_key = upload[0].as<std::string>();
        if (upload[3].as<std::string>() != attempt_id ||
            !is_expected_object_key(object_key)) {
          throw std::runtime_error(
              "task upload authorization has invalid attempt lineage");
        }
      }

      json options = json::object();
      json resource_budget = json::object();
      std::string traceparent;
      try {
        const auto params = json::parse(row[5].as<std::string>());
        options = params.value("options", json::object());
        resource_budget = params.value("resource_budget", json::object());
        if (params.contains("_trace") && params.at("_trace").is_object()) {
          traceparent = bounded_text(
              params.at("_trace").value("traceparent", std::string{}), 55);
          if (!valid_traceparent(traceparent)) traceparent.clear();
        }
      } catch (...) {}
      const std::string trace_id =
          traceparent.empty() ? "" : traceparent.substr(3, 32);

      tx.exec_params(
          "UPDATE tasks SET status='RUNNING',status_reason=$2,collection_status='DELIVERED',"
          "started_at=COALESCE(started_at,now()) WHERE id=$1",
          task_id, "C++ 控制面下发任务");
      tx.exec_params(
          "INSERT INTO task_status_events(task_id,from_status,to_status,reason,actor,metadata,created_at) "
          "VALUES($1,'PENDING','RUNNING',$2,'server',$3::jsonb,now())",
          task_id, "C++ 控制面下发任务",
          (json{{"served_by", "cpp-control"},
                {"task_attempt_id", attempt_id},
                {"traceparent", traceparent},
                {"trace_id", trace_id}}).dump());

      // Persist one concrete execution for every claimed logical task.  AI
      // evidence must point to this attempt instead of trusting a task row
      // without execution provenance.
      const std::string attempt_authority = random_task_attempt_authority();
      tx.exec_params(
          "INSERT INTO task_attempts(id,task_id,attempt_no,agent_id,status,reason,"
          "lease_expires_at,metadata_json,task_attempt_authority_sha256,"
          "created_at,started_at) "
          "VALUES($2::varchar,$1::varchar,"
          "(SELECT COALESCE(MAX(attempt_no),0)+1 FROM task_attempts "
          " WHERE task_id=$1::varchar),$3::varchar,'RUNNING',$4::text,"
          "now()+make_interval(secs => $5::integer),$6::jsonb,$7,now(),now())",
          task_id, attempt_id, request->agent_id(),
          "C++ control plane dispatched collection attempt",
          row[4].as<int>() + 30,
          (json{{"served_by", "cpp-control"},
                {"traceparent", traceparent},
                {"trace_id", trace_id}}).dump(),
          sha256_hex(attempt_authority));

      response->set_pending(true);
      auto* desc = response->mutable_task_desc();
      desc->set_task_id(task_id);
      desc->set_task_attempt_authority(attempt_authority);
      desc->set_task_attempt_id(attempt_id);
      desc->set_traceparent(traceparent);
      desc->set_profiler_type(
          static_cast<mini_drop::TaskKindProfiler>(profiler_type(collector)));
      desc->set_timeout_sec(options.value("timeout_sec", row[4].as<int>() + 30));
      desc->set_container_name(options.value("container_name", std::string{}));
      desc->set_container_type(options.value("container_type", 0));
      for (const auto& upload : upload_authorizations) {
        const std::string object_key = upload[0].as<std::string>();
        if (!is_expected_object_key(object_key)) {
          throw std::runtime_error(
              "task upload authorization escaped task object prefix");
        }
        auto* target = desc->add_upload_targets();
        target->set_object_key(object_key);
        target->set_put_url(upload[1].as<std::string>());
        target->set_expires_unix_ms(upload[2].as<std::int64_t>());
      }
      auto* sample = desc->mutable_sample_argv();
      sample->set_pid(row[1].as<int>());
      sample->set_hz(row[3].as<int>());
      sample->set_duration(row[4].as<int>());
      sample->set_callgraph(options.value("callgraph", std::string{"fp"}));
      const std::string go_pprof_endpoint = env_or(
          "MINI_DROP_GO_PPROF_ENDPOINT",
          "http://go-hotspot:6060/debug/pprof/profile");
      std::string default_event = "cpu-cycles";
      if (collector == "java_async") {
        default_event = "cpu";
      } else if (collector == "go_pprof") {
        default_event = go_pprof_endpoint;
      }
      sample->set_event(options.value("event", default_event));
      sample->set_subprocess(options.value("subprocess", false));
      auto* budget = desc->mutable_resource_budget();
      budget->set_max_cpu_percent(
          resource_budget.value("max_cpu_percent", 50));
      budget->set_max_memory_mb(
          resource_budget.value("max_memory_mb", 1024));
      budget->set_max_output_mb(
          resource_budget.value("max_output_mb", 256));
      budget->set_max_duration_sec(
          resource_budget.value("max_duration_sec", row[4].as<int>()));

      const int pid = row[1].as<int>();
      const int hz = row[3].as<int>();
      const int duration = row[4].as<int>();
      if (collector == "perf_cpu") {
        auto* payload = desc->mutable_perf();
        payload->set_pid(pid);
        payload->set_hz(hz);
        payload->set_duration_sec(duration);
        payload->set_callgraph(options.value("callgraph", std::string{"fp"}));
        payload->set_event(options.value("event", std::string{"cpu-cycles"}));
        payload->set_subprocess(options.value("subprocess", false));
      } else if (collector == "java_async") {
        auto* payload = desc->mutable_async_profiler();
        payload->set_pid(pid);
        payload->set_duration_sec(duration);
        payload->set_event(options.value("event", std::string{"cpu"}));
      } else if (collector == "go_pprof") {
        auto* payload = desc->mutable_pprof();
        payload->set_pid(pid);
        payload->set_duration_sec(duration);
        payload->set_endpoint(options.value("pprof_url", go_pprof_endpoint));
      } else if (collector == "ebpf_io") {
        auto* payload = desc->mutable_ebpf();
        payload->set_pid(pid);
        payload->set_duration_sec(duration);
        payload->set_device(options.value("device", std::string{}));
      } else if (collector == "pyspy") {
        auto* payload = desc->mutable_pyspy();
        payload->set_pid(pid);
        payload->set_hz(hz);
        payload->set_duration_sec(duration);
        payload->set_subprocess(options.value("subprocess", false));
      } else if (collector == "memory_smaps") {
        auto* payload = desc->mutable_memory_smaps();
        payload->set_pid(pid);
        payload->set_duration_sec(duration);
        payload->set_interval_ms(options.value("interval_ms", 1000));
      } else if (collector == "sys_metrics") {
        auto* payload = desc->mutable_system_metrics();
        payload->set_pid(pid);
        payload->set_duration_sec(duration);
        payload->set_interval_ms(options.value("interval_ms", 1000));
      } else if (collector == "continuous_perf") {
        auto* payload = desc->mutable_continuous_perf();
        payload->set_pid(pid);
        payload->set_hz(hz);
        payload->set_duration_sec(duration);
        payload->set_window_seconds(
            options.value("window_seconds", duration));
        payload->set_callgraph(options.value("callgraph", std::string{"fp"}));
        payload->set_event(options.value("event", std::string{"cpu-cycles"}));
        payload->set_trigger_cpu_percent(
            options.value("trigger_cpu_percent", 0));
        payload->set_trigger_consecutive_samples(
            options.value("trigger_consecutive_samples", 3));
        payload->set_trigger_wait_seconds(
            options.value("trigger_wait_seconds", 0));
        payload->set_retention_tier(
            options.value("retention_tier", std::string{"standard"}));
      }
      tx.commit();
      return grpc::Status::OK;
    } catch (const std::exception& error) {
      return grpc::Status(grpc::StatusCode::INTERNAL, error.what());
    }
  }
};

class ResultService final : public mini_drop::Hotmethod::Service, private ServiceBase {
 public:
  explicit ResultService(const Config& config) : ServiceBase(config) {}

  grpc::Status NotifyResult(
      grpc::ServerContext* context,
      const mini_drop::TaskResult* request,
      google::protobuf::Empty*) override {
    if (const auto status = authorize(context); !status.ok()) return status;
    try {
      auto connection = database();
      pqxx::work tx(connection);
      const auto tasks = tx.exec_params(
          "SELECT status,collector_type,agent_id FROM tasks WHERE id=$1 FOR UPDATE", request->task_id());
      if (tasks.empty()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND, "task not found");
      }
      const std::string current = tasks[0][0].as<std::string>();
      const std::string collector = tasks[0][1].as<std::string>();
      const std::string agent_id = tasks[0][2].as<std::string>();
      if (const auto status = authorize_agent(context, agent_id); !status.ok()) {
        return status;
      }
      const auto attempts = tx.exec_params(
          "SELECT id,task_attempt_authority_sha256,"
          "COALESCE(metadata_json,'{}'::json)::text FROM task_attempts "
          "WHERE task_id=$1 ORDER BY attempt_no DESC LIMIT 1",
          request->task_id());
      if (attempts.empty() || attempts[0][1].is_null() ||
          request->task_attempt_authority().empty() ||
          !secure_equal(
              attempts[0][1].as<std::string>(),
              sha256_hex(request->task_attempt_authority()))) {
        return grpc::Status(
            grpc::StatusCode::PERMISSION_DENIED,
            "invalid or stale task-attempt authority");
      }
      const std::string attempt_id = attempts[0][0].as<std::string>();
      json attempt_metadata = json::object();
      try {
        attempt_metadata = json::parse(attempts[0][2].as<std::string>());
      } catch (...) {}
      attempt_metadata["served_by"] = "cpp-control";
      attempt_metadata["task_attempt_id"] = attempt_id;
      if (current == "CANCELLED" || current == "ANALYZING" || current == "DONE" || current == "FAILED") {
        tx.commit();
        return grpc::Status::OK;
      }
      const auto fail_attempt = [&](std::string error_code,
                                    std::string message) -> grpc::Status {
        message = message.substr(0, 1024);
        if (mini_drop_contract::find_error_code(error_code) == nullptr ||
            error_code == mini_drop_contract::kErrorNone) {
          error_code = std::string(mini_drop_contract::kErrorInternalError);
        }
        json metadata = attempt_metadata;
        metadata["error_code"] = error_code;
        tx.exec_params(
            "UPDATE tasks SET status='FAILED',status_reason=$2,collection_status='FAILED',"
            "analysis_status='CANCELED',error_code=$3,error_message=$2,"
            "finished_at=now() WHERE id=$1",
            request->task_id(), message, error_code);
        tx.exec_params(
            "UPDATE task_attempts SET status='FAILED',reason=$2,finished_at=now(),"
            "metadata_json=$3::json WHERE id=$1",
            attempt_id, message, metadata.dump());
        tx.exec_params(
            "INSERT INTO task_status_events(task_id,from_status,to_status,reason,actor,metadata,created_at) "
            "VALUES($1,$2,'FAILED',$3,'agent',$4::jsonb,now())",
            request->task_id(), current, message, metadata.dump());
        tx.commit();
        return grpc::Status::OK;
      };
      if (!request->error_message().empty()) {
        const auto* contract = mini_drop_contract::find_error_code(
            static_cast<int>(request->error_code()));
        return fail_attempt(
            contract == nullptr
                ? std::string(mini_drop_contract::kErrorInternalError)
                : std::string(contract->name),
            request->error_message());
      }

      tx.exec_params(
          "UPDATE tasks SET status='UPLOADING',status_reason=$2,"
          "collection_status='UPLOADING' WHERE id=$1",
          request->task_id(), "C++ 控制面接收采集产物");
      tx.exec_params(
          "INSERT INTO task_status_events(task_id,from_status,to_status,reason,actor,metadata,created_at) "
          "VALUES($1,$2,'UPLOADING',$3,'agent',$4::jsonb,now())",
          request->task_id(), current, "C++ 控制面接收采集产物",
          attempt_metadata.dump());

      json artifacts = json::array();
      try {
        artifacts = json::parse(request->artifact_metadata_json());
      } catch (...) {
        if (!request->cos_key().empty()) {
          artifacts.push_back({{"artifact_type", request->artifact_type().empty() ? "raw" : request->artifact_type()},
                               {"object_key", request->cos_key()}});
        }
      }
      std::vector<int> artifact_ids;
      for (const auto& artifact : artifacts) {
        if (!artifact.is_object()) continue;
        const std::string object_key = artifact.value(
            "object_key", artifact.value("cos_key", std::string{}));
        if (object_key.empty()) continue;
        const auto authorized = tx.exec_params(
            "UPDATE task_upload_authorizations SET used_at=COALESCE(used_at,now()) "
            "WHERE task_id=$1 AND task_attempt_id=$2 AND object_key=$3 RETURNING id",
            request->task_id(), attempt_id, object_key);
        if (authorized.empty()) {
          return grpc::Status(
              grpc::StatusCode::PERMISSION_DENIED,
              "artifact object is outside task-scoped upload authorization");
        }
        const auto inserted = tx.exec_params(
            "INSERT INTO artifacts(task_id,task_attempt_id,artifact_type,bucket,object_key,filename,local_path,"
            "content_type,size_bytes,sha256,manifest_json,integrity_status,integrity_reason,metadata,created_at) "
            "VALUES($1,$2,$3,$4,$5,$6,NULL,$7,$8,NULLIF($9,''),$10::json,"
            "CASE WHEN length($9)=64 THEN 'DECLARED' ELSE 'LEGACY_UNVERIFIED' END,"
            "CASE WHEN length($9)=64 THEN 'Agent supplied SHA-256; awaiting Analyzer verification' "
            "ELSE 'Agent did not supply SHA-256' END,$11::json,now()) RETURNING id",
            request->task_id(), attempt_id,
            artifact.value("artifact_type", std::string{"raw"}),
            artifact.value("bucket", config_.minio_bucket), object_key,
            artifact.value("filename", std::string{}),
            artifact.value("content_type", std::string{"application/octet-stream"}),
            artifact.value("size_bytes", 0), artifact.value("sha256", std::string{}),
            artifact.value("manifest", json::object()).dump(),
            artifact.value("metadata", json::object()).dump());
        artifact_ids.push_back(inserted[0][0].as<int>());
      }
      if (artifact_ids.empty()) {
        return fail_attempt(
            std::string(mini_drop_contract::kErrorResultMalformed),
            "collector result contains no valid artifacts");
      }

      tx.exec_params(
          "UPDATE task_attempts SET status='SUCCEEDED',reason=$2,finished_at=now(),"
          "metadata_json=$3::json "
          "WHERE id=(SELECT id FROM task_attempts WHERE task_id=$1 "
          "ORDER BY attempt_no DESC LIMIT 1)",
          request->task_id(), "Collection artifacts persisted",
          attempt_metadata.dump());

      const std::string metadata = artifacts.dump();
      const std::string checksum = sha256_hex(metadata);
      const std::string job_id = random_id("analysis_");
      // Route to the collector-aware analyzer contract so the Python
      // Analyzer validates the artifact set and (for perf/pprof/pyspy)
      // generates flamegraph/top outputs instead of passing raw blobs through.
      const std::string analyzer_type =
          collector.empty() ? "artifact-set" : ("collector." + collector);
      const std::string analyzer_version = "1.0.0";
      const std::string key = request->task_id() + ":" + attempt_id + ":" +
          analyzer_type + ":" + analyzer_version + ":" + checksum;
      json ids = artifact_ids;
      tx.exec_params(
          "INSERT INTO analysis_jobs(id,task_id,task_attempt_id,analyzer_type,analyzer_version,input_checksum,"
          "input_artifact_ids_json,idempotency_key,status,status_reason,retry_count,max_retries,next_run_at,"
          "output_artifact_ids_json,created_at,updated_at) "
          "VALUES($1,$2,$3,$8,$9,$4,$5::jsonb,$6,'PENDING',$7,0,3,now(),'[]'::jsonb,now(),now()) "
          "ON CONFLICT(idempotency_key) DO UPDATE SET updated_at=excluded.updated_at "
          "RETURNING id",
          job_id, request->task_id(), attempt_id, checksum, ids.dump(), key,
          "C++ 控制面已持久化采集产物，等待 Python Analyzer",
          analyzer_type, analyzer_version);
      for (const int artifact_id : artifact_ids) {
        tx.exec_params(
            "INSERT INTO analysis_job_input_artifacts("
            "analysis_job_id,artifact_id,task_id,task_attempt_id,created_at) "
            "VALUES((SELECT id FROM analysis_jobs WHERE idempotency_key=$1),$2,$3,$4,now()) "
            "ON CONFLICT(analysis_job_id,artifact_id) DO NOTHING",
            key, artifact_id, request->task_id(), attempt_id);
      }
      tx.exec_params(
          "UPDATE tasks SET status='ANALYZING',status_reason=$2,collection_status='COLLECTED',"
          "analysis_status='PENDING' WHERE id=$1",
          request->task_id(), "产物已记录，等待 Python Analyzer");
      tx.exec_params(
          "INSERT INTO task_status_events(task_id,from_status,to_status,reason,actor,metadata,created_at) "
          "VALUES($1,'UPLOADING','ANALYZING',$2,'server',$3::jsonb,now())",
          request->task_id(), "产物已记录，等待 Python Analyzer",
          attempt_metadata.dump());
      tx.commit();
      return grpc::Status::OK;
    } catch (const std::exception& error) {
      return grpc::Status(grpc::StatusCode::INTERNAL, error.what());
    }
  }
};

}  // namespace

int main(int argc, char** argv) {
  Config config;
  try {
    config = load_config();
  } catch (const std::exception& error) {
    std::cerr << R"({"level":"error","event":"control_config_invalid","error":")"
              << error.what() << R"("})" << std::endl;
    return 2;
  }
  if (argc > 1 && std::string(argv[1]) == "--healthcheck") {
    try {
      auto channel = health_channel(config);
      const bool ready = channel->WaitForConnected(
          std::chrono::system_clock::now() + std::chrono::seconds(3));
      return ready ? 0 : 1;
    } catch (const std::exception& error) {
      std::cerr << R"({"level":"error","event":"control_health_tls_failed","error":")"
                << error.what() << R"("})" << std::endl;
      return 1;
    }
  }

  InitService init(config);
  HealthService health(config);
  ResultService result(config);
  ControlService control(config);
  grpc::ServerBuilder builder;
  try {
    grpc::EnableDefaultHealthCheckService(true);
    builder.AddListeningPort(config.listen_addr, server_credentials(config));
  } catch (const std::exception& error) {
    std::cerr << R"({"level":"error","event":"control_tls_init_failed","error":")"
              << error.what() << R"("})" << std::endl;
    return 2;
  }
  builder.RegisterService(&init);
  builder.RegisterService(&health);
  builder.RegisterService(&result);
  builder.RegisterService(&control);
  std::unique_ptr<grpc::Server> server(builder.BuildAndStart());
  if (!server) {
    std::cerr << R"({"level":"error","event":"control_start_failed"})" << std::endl;
    return 1;
  }
  std::cout << R"({"level":"info","event":"cpp_control_started","addr":")"
            << config.listen_addr << R"("})" << std::endl;
  std::thread(run_maintenance, config).detach();
  server->Wait();
  return 0;
}
