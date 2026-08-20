#include <grpcpp/grpcpp.h>

#include "healthcheck.grpc.pb.h"
#include "hotmethod.grpc.pb.h"
#include "init.grpc.pb.h"

#include <google/protobuf/empty.pb.h>
#include <nlohmann/json.hpp>
#include <pqxx/pqxx>

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <memory>
#include <random>
#include <sstream>
#include <string>
#include <thread>
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

std::string stable_checksum(const std::string& input) {
  std::uint64_t hash = 1469598103934665603ULL;
  for (const unsigned char ch : input) {
    hash ^= ch;
    hash *= 1099511628211ULL;
  }
  std::ostringstream part;
  part << std::hex << std::setw(16) << std::setfill('0') << hash;
  const std::string value = part.str();
  return value + value + value + value;
}

struct Config {
  std::string listen_addr;
  std::string database_url;
  std::string grpc_token;
  bool auth_enabled;
  std::string minio_endpoint;
  std::string minio_access;
  std::string minio_secret;
  std::string minio_bucket;
  bool distribute_credentials;
  int agent_offline_timeout_sec;
  int maintenance_interval_sec;
};

Config load_config() {
  return Config{
      env_or("NATIVE_CONTROL_LISTEN_ADDR", "0.0.0.0:50052"),
      normalize_database_url(env_or(
          "DATABASE_URL", "postgresql://mini_drop:mini_drop@postgres:5432/mini_drop")),
      env_or("MINI_DROP_GRPC_TOKEN", ""),
      env_bool("MINI_DROP_GRPC_AUTH_ENABLED"),
      env_or("MINIO_AGENT_ENDPOINT", env_or("MINIO_ENDPOINT", "minio:9000")),
      env_or("MINIO_ACCESS_KEY", ""),
      env_or("MINIO_SECRET_KEY", ""),
      env_or("MINIO_BUCKET", "mini-drop"),
      env_bool("MINI_DROP_GRPC_DISTRIBUTE_MINIO_CREDENTIALS") &&
          env_bool("MINI_DROP_GRPC_SECURE"),
      env_int("AGENT_OFFLINE_TIMEOUT_SEC", 30),
      env_int("MINI_DROP_CONTROL_MAINTENANCE_SEC", 5)};
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
    if (const auto status = authorize(context); !status.ok()) return status;
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
      const mini_drop::FetchConfigRequest*,
      mini_drop::FetchConfigResponse* response) override {
    if (const auto status = authorize(context); !status.ok()) return status;
    auto* cos = response->mutable_cos_config();
    cos->set_endpoint(config_.minio_endpoint);
    cos->set_bucket(config_.minio_bucket);
    if (config_.distribute_credentials) {
      cos->set_access_key(config_.minio_access);
      cos->set_secret_key(config_.minio_secret);
    }
    return grpc::Status::OK;
  }
};

int profiler_type(const std::string& collector) {
  if (collector == "java_async") return 1;
  if (collector == "go_pprof") return 2;
  if (collector == "pyspy") return 3;
  if (collector == "ebpf_io") return 4;
  if (collector == "memory_smaps") return 5;
  if (collector == "sys_metrics") return 6;
  if (collector == "continuous_perf") return 7;
  return 0;
}

class HealthService final : public mini_drop::HealthCheck::Service, private ServiceBase {
 public:
  explicit HealthService(const Config& config) : ServiceBase(config) {}

  grpc::Status Do(
      grpc::ServerContext* context,
      const mini_drop::HealthCheckRequest* request,
      mini_drop::HealthCheckResponse* response) override {
    if (const auto status = authorize(context); !status.ok()) return status;
    response->set_status(mini_drop::HealthCheckResponse::SERVING);
    try {
      auto connection = database();
      pqxx::work tx(connection);
      tx.exec_params(
          "UPDATE agents SET ip_addr=CASE WHEN $2='' THEN ip_addr ELSE $2 END,status='ONLINE',"
          "last_heartbeat_at=now(),updated_at=now() WHERE id=$1",
          request->agent_id(), request->ip_addr());

      if (request->busy()) {
        if (!request->active_task_id().empty()) {
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
          "SELECT id,target_pid,collector_type,sample_rate,duration_sec,request_params "
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
      tx.exec_params(
          "UPDATE tasks SET status='RUNNING',status_reason=$2,collection_status='RUNNING',"
          "started_at=COALESCE(started_at,now()) WHERE id=$1",
          task_id, "C++ 控制面下发任务");
      tx.exec_params(
          "INSERT INTO task_status_events(task_id,from_status,to_status,reason,actor,metadata,created_at) "
          "VALUES($1,'PENDING','RUNNING',$2,'server',$3::jsonb,now())",
          task_id, "C++ 控制面下发任务", R"({"served_by":"cpp-control"})");

      // Persist one concrete execution for every claimed logical task.  AI
      // evidence must point to this attempt instead of trusting a task row
      // without execution provenance.
      tx.exec_params(
          "INSERT INTO task_attempts(id,task_id,attempt_no,agent_id,status,reason,"
          "lease_expires_at,metadata_json,created_at,started_at) "
          "VALUES($2::varchar,$1::varchar,"
          "(SELECT COALESCE(MAX(attempt_no),0)+1 FROM task_attempts "
          " WHERE task_id=$1::varchar),$3::varchar,'RUNNING',$4::text,"
          "now()+make_interval(secs => $5::integer),$6::jsonb,now(),now())",
          task_id, random_id("attempt_"), request->agent_id(),
          "C++ control plane dispatched collection attempt",
          row[4].as<int>() + 30, R"({"served_by":"cpp-control"})");

      json options = json::object();
      try {
        const auto params = json::parse(row[5].as<std::string>());
        options = params.value("options", json::object());
      } catch (...) {}

      response->set_pending(true);
      auto* desc = response->mutable_task_desc();
      desc->set_task_id(task_id);
      desc->set_profiler_type(profiler_type(collector));
      desc->set_timeout_sec(options.value("timeout_sec", row[4].as<int>() + 30));
      desc->set_container_name(options.value("container_name", std::string{}));
      desc->set_container_type(options.value("container_type", 0));
      auto* sample = desc->mutable_sample_argv();
      sample->set_pid(row[1].as<int>());
      sample->set_hz(row[3].as<int>());
      sample->set_duration(row[4].as<int>());
      sample->set_callgraph(options.value("callgraph", std::string{"fp"}));
      std::string default_event = "cpu-cycles";
      if (collector == "java_async") {
        default_event = "cpu";
      } else if (collector == "go_pprof") {
        default_event = "http://go-hotspot:6060/debug/pprof/profile";
      }
      sample->set_event(options.value("event", default_event));
      sample->set_subprocess(options.value("subprocess", false));
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
          "SELECT status,collector_type FROM tasks WHERE id=$1 FOR UPDATE", request->task_id());
      if (tasks.empty()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND, "task not found");
      }
      const std::string current = tasks[0][0].as<std::string>();
      const std::string collector = tasks[0][1].as<std::string>();
      if (current == "CANCELLED" || current == "ANALYZING" || current == "DONE" || current == "FAILED") {
        tx.commit();
        return grpc::Status::OK;
      }
      if (!request->error_message().empty()) {
        tx.exec_params(
            "UPDATE tasks SET status='FAILED',status_reason=$2,collection_status='FAILED',"
            "analysis_status='NOT_STARTED',finished_at=now() WHERE id=$1",
            request->task_id(), request->error_message().substr(0, 1024));
        tx.exec_params(
            "UPDATE task_attempts SET status='FAILED',reason=$2,finished_at=now(),"
            "metadata_json=$3::json "
            "WHERE id=(SELECT id FROM task_attempts WHERE task_id=$1 "
            "ORDER BY attempt_no DESC LIMIT 1)",
            request->task_id(), request->error_message().substr(0, 1024),
            R"({"served_by":"cpp-control"})");
        tx.exec_params(
            "INSERT INTO task_status_events(task_id,from_status,to_status,reason,actor,metadata,created_at) "
            "VALUES($1,$2,'FAILED',$3,'agent',$4::jsonb,now())",
            request->task_id(), current, request->error_message().substr(0, 1024),
            R"({"served_by":"cpp-control"})");
        tx.commit();
        return grpc::Status::OK;
      }

      tx.exec_params(
          "UPDATE tasks SET status='UPLOADING',status_reason=$2 WHERE id=$1",
          request->task_id(), "C++ 控制面接收采集产物");
      tx.exec_params(
          "INSERT INTO task_status_events(task_id,from_status,to_status,reason,actor,metadata,created_at) "
          "VALUES($1,$2,'UPLOADING',$3,'agent',$4::jsonb,now())",
          request->task_id(), current, "C++ 控制面接收采集产物",
          R"({"served_by":"cpp-control"})");

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
        const auto inserted = tx.exec_params(
            "INSERT INTO artifacts(task_id,artifact_type,bucket,object_key,filename,local_path,"
            "content_type,size_bytes,sha256,manifest_json,integrity_status,integrity_reason,metadata,created_at) "
            "VALUES($1,$2,$3,$4,$5,NULL,$6,$7,NULLIF($8,''),$9::json,"
            "CASE WHEN length($8)=64 THEN 'DECLARED' ELSE 'LEGACY_UNVERIFIED' END,"
            "CASE WHEN length($8)=64 THEN 'Agent supplied SHA-256; awaiting Analyzer verification' "
            "ELSE 'Agent did not supply SHA-256' END,$10::json,now()) RETURNING id",
            request->task_id(), artifact.value("artifact_type", std::string{"raw"}),
            artifact.value("bucket", config_.minio_bucket), object_key,
            artifact.value("filename", std::string{}),
            artifact.value("content_type", std::string{"application/octet-stream"}),
            artifact.value("size_bytes", 0), artifact.value("sha256", std::string{}),
            artifact.value("manifest", json::object()).dump(),
            artifact.value("metadata", json::object()).dump());
        artifact_ids.push_back(inserted[0][0].as<int>());
      }
      if (artifact_ids.empty()) {
        throw std::runtime_error("collector result contains no valid artifacts");
      }

      tx.exec_params(
          "UPDATE task_attempts SET status='SUCCEEDED',reason=$2,finished_at=now(),"
          "metadata_json=$3::json "
          "WHERE id=(SELECT id FROM task_attempts WHERE task_id=$1 "
          "ORDER BY attempt_no DESC LIMIT 1)",
          request->task_id(), "Collection artifacts persisted",
          R"({"served_by":"cpp-control"})");

      const std::string metadata = artifacts.dump();
      const std::string checksum = stable_checksum(metadata);
      const std::string job_id = random_id("analysis_");
      // Route to the collector-aware analyzer contract so the Python
      // Analyzer validates the artifact set and (for perf/pprof/pyspy)
      // generates flamegraph/top outputs instead of passing raw blobs through.
      const std::string analyzer_type =
          collector.empty() ? "artifact-set" : ("collector." + collector);
      const std::string analyzer_version = "1.0.0";
      const std::string key = request->task_id() + ":" + analyzer_type + ":" + analyzer_version + ":" + checksum;
      json ids = artifact_ids;
      tx.exec_params(
          "INSERT INTO analysis_jobs(id,task_id,analyzer_type,analyzer_version,input_checksum,"
          "input_artifact_ids_json,idempotency_key,status,status_reason,retry_count,max_retries,next_run_at,"
          "output_artifact_ids_json,created_at,updated_at) "
          "VALUES($1,$2,$7,$8,$3,$4::jsonb,$5,'PENDING',$6,0,3,now(),'[]'::jsonb,now(),now()) "
          "ON CONFLICT(idempotency_key) DO NOTHING",
          job_id, request->task_id(), checksum, ids.dump(), key,
          "C++ 控制面已持久化采集产物，等待 Python Analyzer",
          analyzer_type, analyzer_version);
      tx.exec_params(
          "UPDATE tasks SET status='ANALYZING',status_reason=$2,collection_status='SUCCEEDED',"
          "analysis_status='QUEUED' WHERE id=$1",
          request->task_id(), "产物已记录，等待 Python Analyzer");
      tx.exec_params(
          "INSERT INTO task_status_events(task_id,from_status,to_status,reason,actor,metadata,created_at) "
          "VALUES($1,'UPLOADING','ANALYZING',$2,'server',$3::jsonb,now())",
          request->task_id(), "产物已记录，等待 Python Analyzer",
          R"({"served_by":"cpp-control"})");
      tx.commit();
      return grpc::Status::OK;
    } catch (const std::exception& error) {
      return grpc::Status(grpc::StatusCode::INTERNAL, error.what());
    }
  }
};

}  // namespace

int main(int argc, char** argv) {
  const Config config = load_config();
  if (argc > 1 && std::string(argv[1]) == "--healthcheck") {
    auto channel = grpc::CreateChannel(
        env_or("NATIVE_CONTROL_HEALTH_ADDR", "127.0.0.1:50052"),
        grpc::InsecureChannelCredentials());
    const bool ready = channel->WaitForConnected(
        std::chrono::system_clock::now() + std::chrono::seconds(3));
    return ready ? 0 : 1;
  }

  InitService init(config);
  HealthService health(config);
  ResultService result(config);
  grpc::ServerBuilder builder;
  builder.AddListeningPort(config.listen_addr, grpc::InsecureServerCredentials());
  builder.RegisterService(&init);
  builder.RegisterService(&health);
  builder.RegisterService(&result);
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
