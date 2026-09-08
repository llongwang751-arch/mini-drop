// Mini-Drop data-plane Agent.
//
// One instance runs on each Linux worker. Its loop registers with Control,
// publishes process snapshots and resource metrics, pulls a validated TaskDesc,
// executes a whitelisted Collector, uploads artifacts, and reports the result.
// Keep authority checks in this process: a browser- or model-supplied PID alone
// is never enough to attach to a process.
#include <grpcpp/grpcpp.h>

#include "healthcheck.grpc.pb.h"
#include "hotmethod.grpc.pb.h"
#include "init.grpc.pb.h"
#include "config.h"
#include "task.h"
#include "process_runner.h"
#include "artifact_uploader.h"
#include "collector_registry.h"
#include "process_snapshot.h"
#include "result_outbox.h"
#include "error_code_contract.h"

#include <atomic>
#include <algorithm>
#include <chrono>
#include <cctype>
#include <csignal>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <unistd.h>

namespace fs = std::filesystem;
using namespace std::chrono_literals;
using mini_drop_native::Config;
using mini_drop_native::Task;
using mini_drop_native::TaskResult;
using mini_drop_native::ResultOutbox;
using mini_drop_native::load_config;
using mini_drop_native::ProcessGroupRunner;
using mini_drop_native::CommandResult;
using mini_drop_native::authorized_object_key;
using mini_drop_native::sha256_file;
using mini_drop_native::upload_artifact;
using mini_drop_native::CollectorRegistry;
using mini_drop_native::ProcessSnapshot;
using mini_drop_native::collect_process_snapshot;
using mini_drop_native::make_default_collector_registry;

namespace {

std::atomic<bool> g_stop{false};

void on_signal(int) { g_stop.store(true); }

std::string hostname() {
  char buffer[256]{};
  if (::gethostname(buffer, sizeof(buffer) - 1) == 0) {
    return buffer;
  }
  return "unknown";
}

std::string read_first_line(const fs::path& path) {
  std::ifstream input(path);
  std::string line;
  std::getline(input, line);
  return line;
}

std::string read_file(const std::string& path) {
  if (path.empty()) return "";
  std::ifstream input(path, std::ios::binary);
  if (!input) throw std::runtime_error("cannot read TLS file: " + path);
  std::ostringstream content;
  content << input.rdbuf();
  return content.str();
}

std::shared_ptr<grpc::Channel> create_control_channel(const Config& config) {
  if (!config.grpc_secure) {
    return grpc::CreateChannel(config.grpc_addr, grpc::InsecureChannelCredentials());
  }
  if (config.grpc_ca_cert.empty()) {
    throw std::runtime_error("AGENT_GRPC_CA_CERT is required when TLS is enabled");
  }
  const bool has_client_cert = !config.grpc_client_cert.empty();
  const bool has_client_key = !config.grpc_client_key.empty();
  if (has_client_cert != has_client_key) {
    throw std::runtime_error(
        "AGENT_GRPC_CLIENT_CERT and AGENT_GRPC_CLIENT_KEY must be configured together");
  }
  grpc::SslCredentialsOptions options;
  options.pem_root_certs = read_file(config.grpc_ca_cert);
  if (has_client_cert) {
    options.pem_cert_chain = read_file(config.grpc_client_cert);
    options.pem_private_key = read_file(config.grpc_client_key);
  }
  grpc::ChannelArguments arguments;
  if (!config.grpc_tls_server_name.empty()) {
    arguments.SetSslTargetNameOverride(config.grpc_tls_server_name);
  }
  return grpc::CreateCustomChannel(
      config.grpc_addr, grpc::SslCredentials(options), arguments);
}

std::string json_escape(const std::string& value) {
  std::ostringstream out;
  for (const unsigned char ch : value) {
    switch (ch) {
      case '\\': out << "\\\\"; break;
      case '"': out << "\\\""; break;
      case '\n': out << "\\n"; break;
      case '\r': out << "\\r"; break;
      case '\t': out << "\\t"; break;
      default:
        if (ch < 0x20) {
          out << "\\u00";
          constexpr char hex[] = "0123456789abcdef";
          out << hex[(ch >> 4) & 0x0f] << hex[ch & 0x0f];
        } else {
          out << ch;
        }
    }
  }
  return out.str();
}

template <typename Request>
grpc::ClientContext make_context(const Config& config) {
  grpc::ClientContext context;
  context.set_deadline(std::chrono::system_clock::now() + 10s);
  if (!config.grpc_token.empty()) {
    context.AddMetadata("x-mini-drop-grpc-token", config.grpc_token);
  }
  return context;
}

bool safe_identity_component(const std::string& value);
std::string classify_error_code(const std::string& error);
bool ensure_private_attempt_work_directory(
    const Task& task, fs::path& work_directory, std::string& error);
bool attach_attempt_manifest(
    const Config& config, const Task& task, TaskResult& result,
    const fs::path& work_directory, std::string& error_code);

TaskResult execute_task(
    const Config& config,
    const Task& task,
    std::atomic<bool>& cancel_requested) {
  if (!safe_identity_component(task.id) ||
      !safe_identity_component(task.task_attempt_id) ||
      task.task_attempt_authority.empty()) {
    TaskResult result;
    result.task_id = task.id;
    result.task_attempt_id = task.task_attempt_id;
    result.task_attempt_authority = task.task_attempt_authority;
    result.error = "task execution identity is missing or unsafe";
    result.error_code = std::string(mini_drop_contract::kErrorInvalidArgument);
    return result;
  }
  if (task.max_cpu_percent < 1 || task.max_cpu_percent > 100 ||
      task.max_memory_mb < 64 || task.max_output_mb < 1 ||
      task.max_duration < task.duration) {
    TaskResult result;
    result.task_id = task.id;
    result.task_attempt_id = task.task_attempt_id;
    result.task_attempt_authority = task.task_attempt_authority;
    result.error = "task resource budget is invalid or below requested duration";
    result.error_code = std::string(mini_drop_contract::kErrorInvalidArgument);
    return result;
  }
  static const CollectorRegistry registry = make_default_collector_registry();
  const auto* collector = registry.find(task.profiler_type);
  if (collector == nullptr) {
    TaskResult result;
    result.task_id = task.id;
    result.task_attempt_authority = task.task_attempt_authority;
    result.task_attempt_id = task.task_attempt_id;
    result.error = "native C++ Agent has no collector plugin for profiler_type=" +
        std::to_string(task.profiler_type);
    result.error_code = std::string(mini_drop_contract::kErrorTaskKindUnsupported);
    return result;
  }
  fs::path work_directory;
  std::string work_directory_error;
  if (!ensure_private_attempt_work_directory(
          task, work_directory, work_directory_error)) {
    TaskResult result;
    result.task_id = task.id;
    result.task_attempt_authority = task.task_attempt_authority;
    result.task_attempt_id = task.task_attempt_id;
    result.error = work_directory_error;
    result.error_code = std::string(mini_drop_contract::kErrorArtifactPathInvalid);
    return result;
  }
  Config effective_config = config;
  effective_config.max_memory_mb = std::min(
      config.max_memory_mb, task.max_memory_mb);
  effective_config.max_output_mb = std::min(
      config.max_output_mb, task.max_output_mb);
  TaskResult result = collector->collect(
      effective_config, task, g_stop, cancel_requested);
  result.task_id = task.id;
  result.task_attempt_authority = task.task_attempt_authority;
  result.task_attempt_id = task.task_attempt_id;
  if (!result.ok && result.error_code.empty()) {
    result.error_code = classify_error_code(result.error);
  }
  if (result.ok) {
    std::string manifest_error_code;
    if (!attach_attempt_manifest(
            config, task, result, work_directory, manifest_error_code)) {
      result.ok = false;
      result.error_code = manifest_error_code;
    }
  }
  return result;
}

bool safe_identity_component(const std::string& value) {
  if (value.empty() || value == "." || value == "..") return false;
  return std::all_of(value.begin(), value.end(), [](const unsigned char ch) {
    return std::isalnum(ch) || ch == '_' || ch == '-' || ch == '.';
  });
}

bool ensure_private_attempt_work_directory(
    const Task& task, fs::path& work_directory, std::string& error) {
  const fs::path base = "/tmp/mini-drop-native";
  const fs::path task_directory = base / task.id;
  work_directory = task_directory / task.task_attempt_id;
  for (const auto& directory : {base, task_directory, work_directory}) {
    std::error_code filesystem_error;
    fs::create_directory(directory, filesystem_error);
    if (filesystem_error) {
      error = "cannot create private TaskAttempt work directory: " +
          filesystem_error.message();
      return false;
    }
    const auto status = fs::symlink_status(directory, filesystem_error);
    if (filesystem_error || !fs::is_directory(status) || fs::is_symlink(status)) {
      error = "TaskAttempt work path is not a private directory";
      return false;
    }
    fs::permissions(
        directory, fs::perms::owner_all, fs::perm_options::replace,
        filesystem_error);
    if (filesystem_error) {
      error = "cannot restrict TaskAttempt work directory permissions: " +
          filesystem_error.message();
      return false;
    }
  }
  return true;
}

bool attach_attempt_manifest(
    const Config& config, const Task& task, TaskResult& result,
    const fs::path& work_directory, std::string& error_code) {
  if (result.artifact_json.size() < 2 || result.artifact_json.front() != '[' ||
      result.artifact_json.back() != ']') {
    result.error = "collector returned malformed artifact metadata";
    error_code = std::string(mini_drop_contract::kErrorResultMalformed);
    return false;
  }

  const auto completed_at_unix_ms =
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count();
  const fs::path manifest_path = work_directory / "manifest.json";
  std::ostringstream manifest;
  manifest << "{\"schema_version\":\"mini-drop.attempt-manifest.v1\",";
  manifest << "\"task_id\":\"" << json_escape(task.id) << "\",";
  manifest << "\"task_attempt_id\":\""
           << json_escape(task.task_attempt_id) << "\",";
  manifest << "\"traceparent\":\"" << json_escape(task.traceparent) << "\",";
  manifest << "\"profiler_type\":" << task.profiler_type << ',';
  manifest << "\"completed_at_unix_ms\":" << completed_at_unix_ms << ',';
  manifest << "\"artifacts\":" << result.artifact_json << '}';

  std::ofstream output(manifest_path, std::ios::binary | std::ios::trunc);
  output << manifest.str();
  output.flush();
  if (!output) {
    result.error = "failed to write TaskAttempt manifest.json";
    error_code = std::string(mini_drop_contract::kErrorArtifactPathInvalid);
    return false;
  }
  output.close();

  const std::string object_key = authorized_object_key(task, "manifest.json");
  if (object_key.empty()) {
    result.error = "missing exact upload target for manifest.json";
    error_code = std::string(
        mini_drop_contract::kErrorUploadAuthorizationInvalid);
    return false;
  }
  const std::string digest = sha256_file(manifest_path);
  if (digest.empty()) {
    result.error = "failed to compute TaskAttempt manifest SHA-256";
    error_code = std::string(mini_drop_contract::kErrorArtifactHashFailed);
    return false;
  }
  std::string upload_error;
  if (!upload_artifact(task, manifest_path, object_key, upload_error)) {
    result.error = upload_error.empty()
        ? "failed to upload TaskAttempt manifest.json"
        : upload_error;
    error_code = result.error.find("authorization") != std::string::npos
        ? std::string(mini_drop_contract::kErrorUploadAuthorizationInvalid)
        : std::string(mini_drop_contract::kErrorUploadFailed);
    return false;
  }

  std::error_code size_error;
  const auto manifest_size = fs::file_size(manifest_path, size_error);
  if (size_error) {
    result.error = "failed to inspect TaskAttempt manifest.json";
    error_code = std::string(mini_drop_contract::kErrorArtifactPathInvalid);
    return false;
  }

  result.artifact_json.pop_back();
  if (result.artifact_json.size() > 1) result.artifact_json.push_back(',');
  std::ostringstream artifact;
  artifact << "{\"artifact_type\":\"manifest\",";
  artifact << "\"filename\":\"manifest.json\",\"bucket\":\""
           << json_escape(config.minio_bucket) << "\",";
  artifact << "\"object_key\":\"" << json_escape(object_key) << "\",";
  artifact << "\"content_type\":\"application/json\",";
  artifact << "\"size_bytes\":" << manifest_size << ',';
  artifact << "\"sha256\":\"" << digest << "\",";
  artifact << "\"manifest\":{\"manifest_version\":"
              "\"mini-drop.artifact.v1\",\"producer\":\"native-cpp\"},";
  artifact << "\"metadata\":{\"schema_version\":"
              "\"mini-drop.attempt-manifest.v1\",\"traceparent\":\""
           << json_escape(task.traceparent) << "\",\"trace_id\":\""
           << json_escape(
                  task.traceparent.size() == 55
                      ? task.traceparent.substr(3, 32)
                      : std::string{})
           << "\"}}";
  result.artifact_json += artifact.str();
  result.artifact_json.push_back(']');
  return true;
}

std::string classify_error_code(const std::string& error) {
  if (error.find("cancel") != std::string::npos) {
    return std::string(mini_drop_contract::kErrorTaskCanceled);
  }
  if (error.find("timed out") != std::string::npos ||
      error.find("timeout") != std::string::npos) {
    return std::string(mini_drop_contract::kErrorRunnerTimeout);
  }
  if (error.find("does not exist") != std::string::npos) {
    return std::string(mini_drop_contract::kErrorTargetNotFound);
  }
  if (error.find("not installed") != std::string::npos ||
      (error.find("tool") != std::string::npos &&
       error.find("unavailable") != std::string::npos)) {
    return std::string(mini_drop_contract::kErrorRunnerNotFound);
  }
  if (error.find("authorization") != std::string::npos ||
      error.find("upload target") != std::string::npos) {
    return std::string(mini_drop_contract::kErrorUploadAuthorizationInvalid);
  }
  if (error.find("upload") != std::string::npos) {
    return std::string(mini_drop_contract::kErrorUploadFailed);
  }
  if (error.find("SHA-256") != std::string::npos) {
    return std::string(mini_drop_contract::kErrorArtifactHashFailed);
  }
  if (error.find("duration") != std::string::npos ||
      error.find("parameters") != std::string::npos) {
    return std::string(mini_drop_contract::kErrorInvalidArgument);
  }
  if (error.find("failed (exit=") != std::string::npos ||
      error.find("record failed") != std::string::npos) {
    return std::string(mini_drop_contract::kErrorRunnerExitNonzero);
  }
  return std::string(mini_drop_contract::kErrorInternalError);
}

void cleanup_attempt_work_directory(const TaskResult& result) {
  if (!safe_identity_component(result.task_id) ||
      !safe_identity_component(result.task_attempt_id)) {
    return;
  }
  const fs::path base = "/tmp/mini-drop-native";
  const fs::path target = base / result.task_id / result.task_attempt_id;
  std::error_code error;
  fs::remove_all(target, error);
  if (!error) {
    const fs::path task_dir = base / result.task_id;
    fs::remove(task_dir, error);  // only removes the now-empty task directory
  }
}

void add_auth(grpc::ClientContext& context, const Config& config) {
  context.set_deadline(std::chrono::system_clock::now() + 10s);
  if (!config.grpc_token.empty()) {
    context.AddMetadata("x-mini-drop-grpc-token", config.grpc_token);
  }
}

bool register_agent(
    mini_drop::InitAgent::Stub& stub, Config& config) {
  mini_drop::RegisterAgentRequest request;
  request.set_agent_id(config.agent_id);
  request.set_hostname(hostname());
  request.set_ip_addr(config.agent_ip);
  request.set_version("0.3.0-native-cpp");
  request.set_os_info(read_first_line("/proc/version"));
  const CollectorRegistry registry = make_default_collector_registry();
  for (const auto& capability : registry.capabilities()) {
    request.add_capabilities(capability);
  }
  request.add_capabilities("collector_registry");
  request.add_capabilities("native_runner");
  request.add_capabilities("process_group_cancel");

  mini_drop::RegisterAgentResponse response;
  grpc::ClientContext context;
  add_auth(context, config);
  const grpc::Status status = stub.RegisterAgent(&context, request, &response);
  if (!status.ok()) {
    std::cerr << "{\"level\":\"error\",\"event\":\"register_failed\","
              << "\"message\":\"" << json_escape(status.error_message())
              << "\"}\n";
    return false;
  }
  if (response.heartbeat_interval_sec() > 0) {
    config.heartbeat_sec = response.heartbeat_interval_sec();
  }

  mini_drop::FetchConfigRequest fetch_request;
  fetch_request.set_agent_id(config.agent_id);
  mini_drop::FetchConfigResponse fetch_response;
  grpc::ClientContext fetch_context;
  add_auth(fetch_context, config);
  if (stub.FetchConfig(&fetch_context, fetch_request, &fetch_response).ok()) {
    const auto& cos = fetch_response.cos_config();
    if (!cos.bucket().empty()) config.minio_bucket = cos.bucket();
  }
  std::cout << "{\"level\":\"info\",\"event\":\"agent_registered\","
            << "\"agent_id\":\"" << json_escape(config.agent_id) << "\","
            << "\"runtime\":\"native-cpp\"}\n";
  return true;
}

std::optional<mini_drop::HealthCheckResponse> heartbeat(
    mini_drop::HealthCheck::Stub& stub,
    const Config& config,
    bool busy,
    const std::string& active_task_id,
    const std::vector<std::string>& collector_capabilities) {
  mini_drop::HealthCheckRequest request;
  request.set_agent_id(config.agent_id);
  request.set_hostname(hostname());
  request.set_ip_addr(config.agent_ip);
  request.set_agent_version("0.3.0-native-cpp");
  request.set_busy(busy);
  request.set_active_task_id(active_task_id);

  const ProcessSnapshot snapshot = collect_process_snapshot(collector_capabilities);
  auto* wire_snapshot = request.mutable_process_candidate_snapshot();
  wire_snapshot->set_generation(snapshot.generation);
  wire_snapshot->set_boot_id(snapshot.boot_id);
  wire_snapshot->set_observed_at_unix_ms(snapshot.observed_at_unix_ms);
  wire_snapshot->set_complete(snapshot.complete);
  wire_snapshot->set_truncated(snapshot.truncated);
  wire_snapshot->set_error(snapshot.error);
  for (const auto& candidate : snapshot.candidates) {
    auto* wire_candidate = wire_snapshot->add_candidates();
    wire_candidate->set_pid(candidate.pid);
    wire_candidate->set_process_start_ticks(candidate.process_start_ticks);
    wire_candidate->set_pid_namespace_inode(candidate.pid_namespace_inode);
    wire_candidate->set_namespace_pid(candidate.namespace_pid);
    wire_candidate->set_comm(candidate.comm);
    wire_candidate->set_executable_identity(candidate.executable_identity);
    wire_candidate->set_cgroup(candidate.cgroup);
    wire_candidate->set_service_hint(candidate.service_hint);
    wire_candidate->set_instance_hint(candidate.instance_hint);
    for (const auto& capability : candidate.collector_capabilities) {
      wire_candidate->add_collector_capabilities(capability);
    }
  }

  mini_drop::HealthCheckResponse response;
  grpc::ClientContext context;
  add_auth(context, config);
  const grpc::Status status = stub.Do(&context, request, &response);
  if (!status.ok()) {
    std::cerr << "{\"level\":\"error\",\"event\":\"heartbeat_failed\","
              << "\"message\":\"" << json_escape(status.error_message())
              << "\"}\n";
    return std::nullopt;
  }
  return response;
}

bool notify_result(
    mini_drop::Hotmethod::Stub& stub,
    const Config& config,
    const TaskResult& result) {
  mini_drop::TaskResult request;
  request.set_task_id(result.task_id);
  request.set_task_attempt_authority(result.task_attempt_authority);
  const auto* error_contract = mini_drop_contract::find_error_code(
      result.ok ? mini_drop_contract::kErrorNone : std::string_view(result.error_code));
  request.set_error_code(static_cast<mini_drop::ErrorCode>(
      error_contract == nullptr
          ? mini_drop_contract::find_error_code(
                mini_drop_contract::kErrorInternalError)->id
          : error_contract->id));
  if (result.ok) {
    request.set_artifact_type("raw");
    request.set_artifact_metadata_json(result.artifact_json);
  } else {
    request.set_error_message(result.error);
  }
  google::protobuf::Empty response;
  grpc::ClientContext context;
  add_auth(context, config);
  const grpc::Status status = stub.NotifyResult(&context, request, &response);
  if (!status.ok()) {
    std::cerr << "{\"level\":\"error\",\"event\":\"notify_failed\","
              << "\"task_id\":\"" << json_escape(result.task_id) << "\","
              << "\"message\":\"" << json_escape(status.error_message())
              << "\"}\n";
    return false;
  }
  return true;
}

Task task_from_proto(const mini_drop::TaskDesc& desc) {
  Task task;
  task.id = desc.task_id();
  task.profiler_type = static_cast<int>(desc.profiler_type());
  if (desc.has_sample_argv()) {
    task.pid = desc.sample_argv().pid();
    task.hz = static_cast<int>(desc.sample_argv().hz());
    task.duration = static_cast<int>(desc.sample_argv().duration());
    task.callgraph = desc.sample_argv().callgraph();
    task.event = desc.sample_argv().event();
  }
  if (desc.has_perf()) {
    const auto& payload = desc.perf();
    task.pid = payload.pid();
    task.hz = static_cast<int>(payload.hz());
    task.duration = static_cast<int>(payload.duration_sec());
    task.callgraph = payload.callgraph();
    task.event = payload.event();
  } else if (desc.has_async_profiler()) {
    const auto& payload = desc.async_profiler();
    task.pid = payload.pid();
    task.duration = static_cast<int>(payload.duration_sec());
    task.event = payload.event();
  } else if (desc.has_pprof()) {
    const auto& payload = desc.pprof();
    task.pid = payload.pid();
    task.duration = static_cast<int>(payload.duration_sec());
    task.event = payload.endpoint();
  } else if (desc.has_ebpf()) {
    const auto& payload = desc.ebpf();
    task.pid = payload.pid();
    task.duration = static_cast<int>(payload.duration_sec());
    task.event = payload.device();
  } else if (desc.has_pyspy()) {
    const auto& payload = desc.pyspy();
    task.pid = payload.pid();
    task.hz = static_cast<int>(payload.hz());
    task.duration = static_cast<int>(payload.duration_sec());
  } else if (desc.has_memory_smaps()) {
    const auto& payload = desc.memory_smaps();
    task.pid = payload.pid();
    task.duration = static_cast<int>(payload.duration_sec());
  } else if (desc.has_system_metrics()) {
    const auto& payload = desc.system_metrics();
    task.pid = payload.pid();
    task.duration = static_cast<int>(payload.duration_sec());
  } else if (desc.has_continuous_perf()) {
    const auto& payload = desc.continuous_perf();
    task.pid = payload.pid();
    task.hz = static_cast<int>(payload.hz());
    task.duration = static_cast<int>(payload.duration_sec());
    task.callgraph = payload.callgraph();
    task.event = payload.event();
    task.window_seconds = static_cast<int>(payload.window_seconds());
    task.trigger_cpu_percent = static_cast<int>(payload.trigger_cpu_percent());
    task.trigger_consecutive_samples = static_cast<int>(payload.trigger_consecutive_samples());
    task.trigger_wait_seconds = static_cast<int>(payload.trigger_wait_seconds());
    task.retention_tier = payload.retention_tier();
  }
  task.timeout = desc.timeout_sec() > 0
                     ? static_cast<int>(desc.timeout_sec())
                     : task.duration + 30;
  if (desc.has_resource_budget()) {
    task.max_cpu_percent = static_cast<int>(
        desc.resource_budget().max_cpu_percent());
    task.max_memory_mb = static_cast<int>(
        desc.resource_budget().max_memory_mb());
    task.max_output_mb = static_cast<int>(
        desc.resource_budget().max_output_mb());
    task.max_duration = static_cast<int>(
        desc.resource_budget().max_duration_sec());
  } else {
    task.max_duration = task.duration;
  }
  task.container_name = desc.container_name();
  task.task_attempt_authority = desc.task_attempt_authority();
  task.task_attempt_id = desc.task_attempt_id();
  task.traceparent = desc.traceparent();
  for (const auto& target : desc.upload_targets()) {
    if (target.object_key().empty() || target.put_url().empty()) continue;
    task.upload_targets.emplace(
        target.object_key(),
        mini_drop_native::UploadTarget{
            target.put_url(), target.expires_unix_ms()});
  }
  return task;
}

}  // namespace

int main() {
  std::signal(SIGINT, on_signal);
  std::signal(SIGTERM, on_signal);

  Config config;
  try {
    config = load_config();
  } catch (const std::exception& error) {
    std::cerr << "{\"level\":\"error\",\"event\":\"agent_config_invalid\","
              << "\"message\":\"" << json_escape(error.what()) << "\"}\n";
    return 2;
  }
  std::shared_ptr<grpc::Channel> channel;
  try {
    channel = create_control_channel(config);
  } catch (const std::exception& error) {
    std::cerr << "{\"level\":\"error\",\"event\":\"grpc_tls_init_failed\","
              << "\"message\":\"" << json_escape(error.what()) << "\"}\n";
    return 2;
  }
  auto init_stub = mini_drop::InitAgent::NewStub(channel);
  auto health_stub = mini_drop::HealthCheck::NewStub(channel);
  auto result_stub = mini_drop::Hotmethod::NewStub(channel);
  ResultOutbox result_outbox(
      config.result_outbox_dir,
      static_cast<std::size_t>(std::max(1, config.result_outbox_max_entries)));

  while (!g_stop.load() && !register_agent(*init_stub, config)) {
    std::this_thread::sleep_for(2s);
  }

  const auto deliver_pending = [&](const TaskResult& result) {
    const bool acknowledged = notify_result(*result_stub, config, result);
    if (acknowledged) cleanup_attempt_work_directory(result);
    return acknowledged;
  };
  result_outbox.replay(deliver_pending);

  std::mutex result_mutex;
  std::optional<TaskResult> completed;
  std::thread worker;
  std::atomic<bool> cancel_requested{false};
  std::atomic<bool> worker_running{false};
  std::string active_task_id;
  std::string active_traceparent;

  const CollectorRegistry collector_registry = make_default_collector_registry();
  const std::vector<std::string> collector_capabilities =
      collector_registry.capabilities();

  while (!g_stop.load()) {
    {
      std::lock_guard<std::mutex> lock(result_mutex);
      if (completed.has_value()) {
        if (worker.joinable()) worker.join();
        try {
          result_outbox.enqueue(*completed);
        } catch (const std::exception& error) {
          std::cerr << "{\"level\":\"error\",\"event\":\"result_persist_failed\","
                    << "\"task_id\":\"" << json_escape(completed->task_id) << "\","
                    << "\"message\":\"" << json_escape(error.what()) << "\"}\n";
          std::this_thread::sleep_for(1s);
          continue;
        }
        std::cout << "{\"level\":\"info\",\"event\":\"task_finished\","
                  << "\"task_id\":\"" << json_escape(completed->task_id)
                  << "\",\"traceparent\":\"" << json_escape(active_traceparent)
                  << "\",\"ok\":" << (completed->ok ? "true" : "false")
                  << "}\n";
        completed.reset();
        active_task_id.clear();
        active_traceparent.clear();
        worker_running.store(false);
        cancel_requested.store(false);
      }
    }

    result_outbox.replay(deliver_pending);

    const auto response = heartbeat(
        *health_stub, config, worker_running.load(), active_task_id,
        collector_capabilities);
    if (response.has_value()) {
      if (!response->cancel_task_id().empty() &&
          response->cancel_task_id() == active_task_id) {
        cancel_requested.store(true);
      }
      if (!worker_running.load() && response->pending() &&
          !response->task_desc().task_id().empty()) {
        const Task task = task_from_proto(response->task_desc());
        active_task_id = task.id;
        active_traceparent = task.traceparent;
        worker_running.store(true);
        worker = std::thread([&, task]() {
          TaskResult result = execute_task(config, task, cancel_requested);
          std::lock_guard<std::mutex> lock(result_mutex);
          completed = std::move(result);
        });
        std::cout << "{\"level\":\"info\",\"event\":\"task_started\","
                  << "\"task_id\":\"" << json_escape(task.id) << "\","
                  << "\"traceparent\":\"" << json_escape(task.traceparent) << "\","
                  << "\"pid\":" << task.pid << "}\n";
      }
    }
    for (int i = 0; i < config.heartbeat_sec * 10 && !g_stop.load(); ++i) {
      std::this_thread::sleep_for(100ms);
    }
  }

  cancel_requested.store(true);
  if (worker.joinable()) worker.join();
  return 0;
}
