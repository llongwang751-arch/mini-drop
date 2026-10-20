#include "drop.grpc.pb.h"
#include "drop/artifact.hpp"
#include "drop/config.hpp"
#include "drop/cooperative.hpp"
#include "drop/domain.hpp"
#include "drop/helper.hpp"
#include "drop/json.hpp"
#include "drop/manifest.hpp"
#include "drop/process.hpp"
#include "drop/runner.hpp"
#include "drop/sampling.hpp"
#include "drop/target.hpp"
#include "drop/telemetry.hpp"

#include <curl/curl.h>
#include <fcntl.h>
#include <grpcpp/grpcpp.h>
#include <nlohmann/json.hpp>
#include <openssl/rand.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <optional>
#include <regex>
#include <sstream>
#include <thread>
#include <vector>

namespace fs = std::filesystem;
namespace dv = drop::v1;
using json = nlohmann::json;
using namespace std::chrono_literals;

namespace dropcore {
namespace {
std::atomic<bool> alive{true};
void stop(int) { alive = false; }
void deadline(grpc::ClientContext &context) {
  context.set_deadline(std::chrono::system_clock::now() + 5s);
}
std::string perf_executable() {
  const auto configured = secret("PERF_EXECUTABLE");
  if (!configured.empty()) {
    if (configured.front() != '/' || access(configured.c_str(), X_OK) != 0)
      throw std::invalid_argument(
          "PERF_EXECUTABLE must be an existing absolute executable");
    return configured;
  }
  // Ubuntu's /usr/bin/perf wrapper requires an exact host-kernel package. The
  // packaged perf binary can also inspect kernels through the stable perf ABI.
  const fs::path tools("/usr/lib/linux-tools");
  if (fs::exists(tools)) {
    for (const auto &entry : fs::directory_iterator(tools)) {
      auto candidate = entry.path() / "perf";
      if (access(candidate.c_str(), X_OK) == 0 &&
          fs::is_regular_file(candidate))
        return candidate.string();
    }
  }
  return "/usr/bin/perf";
}

class Workload {
public:
  Workload() {
    if (secret("RUN_CONTROLLED_WORKLOAD", "false") != "true")
      return;
    pid = fork();
    if (pid < 0)
      throw std::runtime_error("Cannot launch controlled workload");
    if (pid == 0) {
      prctl(PR_SET_PDEATHSIG, SIGKILL);
      const auto sink = open("/dev/null", O_WRONLY | O_CLOEXEC);
      if (sink < 0 || dup2(sink, STDOUT_FILENO) < 0 ||
          dup2(sink, STDERR_FILENO) < 0)
        _exit(127);
      if (sink > STDERR_FILENO)
        close(sink);
      execl("/usr/local/bin/drop_workload", "drop_workload", nullptr);
      _exit(127);
    }
    std::this_thread::sleep_for(100ms);
    if (kill(pid, 0) != 0)
      throw std::runtime_error("Controlled workload is unavailable");
  }
  ~Workload() {
    if (pid > 1) {
      kill(pid, SIGTERM);
      waitpid(pid, nullptr, 0);
    }
  }
  int pid = 0;
};
} // namespace

class Agent {
  struct Clients {
    std::unique_ptr<dv::InitAgent::Stub> init;
    std::unique_ptr<dv::HealthCheck::Stub> health;
    std::unique_ptr<dv::Hotmethod::Stub> hot;
  };

public:
  Agent()
      : id_(secret("AGENT_ID", "aliyun-local")),
        directory_(secret("WORK_DIR", "/var/lib/drop-agent")),
        perf_(perf_executable()),
        catalog_(secret("TASK_KINDS_FILE", "/etc/drop/task-kinds.json")) {
    if (!safe_identifier(id_) || directory_.empty() ||
        directory_.front() != '/')
      throw std::invalid_argument("Invalid Agent configuration");
    fs::create_directories(directory_);
    chmod(directory_.c_str(), 0700);
    collector_affinity_ = parse_cpu_affinity(secret("COLLECTOR_CPU_AFFINITY"));
    retention_seconds_ =
        std::stoull(secret("RAW_RECOVERY_RETENTION_SECONDS", "86400"));
    recovery_disk_bytes_ =
        std::stoull(secret("RAW_RECOVERY_DISK_BYTES", "1073741824"));
    if (retention_seconds_ < 300 || retention_seconds_ > 604800 ||
        recovery_disk_bytes_ < 128ULL * 1024 * 1024 ||
        recovery_disk_bytes_ > 8ULL * 1024 * 1024 * 1024)
      throw std::invalid_argument("Invalid bounded RAW recovery policy");
    grpc::SslCredentialsOptions tls;
    tls.pem_root_certs = read_file(secret("TLS_CA_FILE"));
    tls.pem_cert_chain = read_file(secret("TLS_CERT_FILE"));
    tls.pem_private_key = read_file(secret("TLS_KEY_FILE"));
    grpc::ChannelArguments args;
    args.SetMaxReceiveMessageSize(1024 * 1024);
    std::istringstream addresses(
        secret("DROP_SERVER_ADDRESSES",
               secret("DROP_SERVER_ADDRESS", "drop-server:50051")));
    std::string address;
    while (std::getline(addresses, address, ',')) {
      if (endpoints_.size() >= 8 ||
          !std::regex_match(address,
                            std::regex("[a-zA-Z0-9._-]+:[0-9]{1,5}")) ||
          std::stoi(address.substr(address.rfind(':') + 1)) > 65535)
        throw std::invalid_argument("Invalid Server address list");
      auto channel =
          grpc::CreateCustomChannel(address, grpc::SslCredentials(tls), args);
      auto endpoint = std::make_shared<Clients>();
      endpoint->init = dv::InitAgent::NewStub(channel);
      endpoint->health = dv::HealthCheck::NewStub(channel);
      endpoint->hot = dv::Hotmethod::NewStub(channel);
      endpoints_.push_back(endpoint);
    }
    if (endpoints_.empty())
      throw std::invalid_argument("No Server address configured");
    clients_.store(endpoints_.front());
    if (secret("COLLECTOR_HELPER_SOCKET").empty())
      probe();
    registry_ = std::make_unique<RunnerRegistry>(catalog_, directory_, perf_);
    if (perf_available_ && !registry_->available("PERF_CPU")) {
      perf_available_ = false;
      perf_reason_ = "Real sampling succeeded but trusted perf tool "
                     "integrity/probe configuration failed";
    }
    if (!secret("COLLECTOR_HELPER_SOCKET").empty()) {
      helper_ = std::make_unique<HelperClient>(
          secret("COLLECTOR_HELPER_SOCKET"), catalog_);
      cooperative_ = std::make_unique<CooperativeFiles>(
          catalog_,
          secret("COLLECTOR_HELPER_INCOMING_DIR", "/run/drop-helper-incoming"),
          id_, std::stoull(secret("COLLECTOR_HELPER_DISK_BYTES", "2147483648")),
          secret("COLLECTOR_HELPER_REQUIRE_BOUNDED_FS", "true") == "true");
      cooperative_->prepare_native_control(
          secret("NATIVE_CONTROL_ROOT",
                 (fs::path(secret("COLLECTOR_HELPER_INCOMING_DIR",
                                  "/run/drop-helper-incoming")) /
                  "native-control")
                     .string()));
    }
    certificate_ = certificate_metadata(secret("TLS_CERT_FILE"));
    metrics_.set("drop_certificate_expires_in_seconds",
                 certificate_.at("expires_in_seconds").get<double>());
    metrics_.set("drop_certificate_not_after_timestamp_seconds",
                 certificate_.at("not_after_unix_seconds").get<double>());
  }

  void run() {
    while (alive && !initialize())
      std::this_thread::sleep_for(3s);
    recover();
    while (alive) {
      dv::HeartbeatRequest request;
      request.set_agent_id(id_);
      request.set_resources_json(resources(request).dump());
      if (negotiated_protocol_ >= 2) {
        std::lock_guard lock(events_mutex_);
        while (!events_.empty() && events_.front().occurred_at_unix_ms() <
                                       unix_ns() / 1000000 - 3600000)
          events_.pop_front();
        for (const auto &event : events_)
          *request.add_events() = event;
      }
      if (attempt_.load())
        request.add_running_attempt_ids(attempt_.load());
      dv::HeartbeatResponse response;
      grpc::ClientContext context;
      deadline(context);
      auto status = clients_.load()->health->Do(&context, request, &response);
      if (status.ok()) {
        {
          std::lock_guard lock(events_mutex_);
          for (const auto &accepted : request.events())
            if (!events_.empty() &&
                events_.front().event_id() == accepted.event_id())
              events_.pop_front();
        }
        heartbeat_failures_ = 0;
        last_heartbeat_ = unix_ns();
        metrics_.add("drop_heartbeats_total");
        metrics_.set("drop_agent_online", 1);
        for (auto attempt : response.cancel_attempt_ids())
          if (attempt == attempt_.load())
            canceled_ = true;
        if (!attempt_.load() && !response.tasks().empty()) {
          if (worker_.joinable())
            worker_.join();
          canceled_ = false;
          attempt_ = response.tasks(0).attempt_id();
          auto task = response.tasks(0);
          worker_ = std::jthread([this, task] {
            execute_task(task);
            attempt_ = 0;
          });
        }
        if (!attempt_.load() && response.tasks().empty() &&
            ++recovery_ticks_ >= 10) {
          if (worker_.joinable())
            worker_.join();
          recovery_ticks_ = 0;
          recover();
        }
      } else {
        metrics_.add("drop_heartbeat_errors_total");
        metrics_.set("drop_agent_online", 0);
        log("drop-agent",
            "Heartbeat unavailable; keeping local execution journal", "",
            "CONTROL_UNAVAILABLE");
        if (++heartbeat_failures_ >= 3 ||
            status.error_code() == grpc::StatusCode::PERMISSION_DENIED)
          initialize();
      }
      metrics_.set("drop_heartbeat_age_seconds",
                   last_heartbeat_ ? (unix_ns() - last_heartbeat_) / 1e9 : 0);
      metrics_.set("drop_runner_active", attempt_ ? 1 : 0);
      if (registration_dirty_)
        initialize();
      for (int i = 0; i < 20 && alive; ++i)
        std::this_thread::sleep_for(100ms);
    }
    canceled_ = true;
    if (worker_.joinable())
      worker_.join();
  }

private:
  LogContext task_context(const dv::TaskDesc &task, const std::string &stage,
                          std::optional<double> duration = std::nullopt) const {
    LogContext context;
    context.request_id = task.request_id();
    context.task_id = task.task_id();
    context.attempt_id = task.attempt_id();
    context.agent_id = id_;
    context.stage = stage;
    context.duration_ms = duration;
    return context;
  }
  void record_event(const std::string &type, const json &payload) {
    if (negotiated_protocol_ < 2)
      return;
    dv::AgentEvent event;
    event.set_agent_id(id_);
    event.set_event_id(id_.substr(0, 64) + "-" + std::to_string(unix_ns()) +
                       "-" + std::to_string(event_sequence_.fetch_add(1)));
    event.set_type(type);
    event.set_payload_json(payload.dump());
    event.set_occurred_at_unix_ms(unix_ns() / 1000000);
    std::lock_guard lock(events_mutex_);
    if (events_.size() == 32) {
      events_.pop_front();
      metrics_.add("drop_agent_events_dropped_total");
    }
    events_.push_back(std::move(event));
  }
  void probe() {
    const auto probe_pid = workload_.pid > 1 ? workload_.pid : getpid();
    auto argv = perf_record_argv(SamplingParameters{probe_pid, 1, 1, "process"},
                                 directory_ + "/probe.data", probe_pid);
    argv[0] = perf_;
    try {
      auto result = execute(argv, directory_ + "/probe.log", 4s);
      perf_available_ = result.exit_code == 0 &&
                        fs::exists(directory_ + "/probe.data") &&
                        fs::file_size(directory_ + "/probe.data") > 0;
      perf_reason_ = perf_available_
                         ? ""
                         : "perf_event_open unavailable: kernel policy, "
                           "capability, seccomp, or tool support";
    } catch (...) {
      perf_reason_ = "perf executable could not be started";
    }
    fs::remove(directory_ + "/probe.data");
    fs::remove(directory_ + "/probe.log");
    log("drop-agent",
        perf_available_ ? "perf capability verified by real sampling probe"
                        : perf_reason_,
        "", perf_available_ ? "" : "PERF_UNAVAILABLE");
  }

  json collection_capabilities() const {
    auto details = registry_->capabilities();
    for (auto &entry : details.items()) {
      if (cooperative_ && cooperative_agent_kind(entry.key())) {
        entry.value()["execution_model"] = "same_uid_agent_cooperation";
        entry.value()["required_target_uid"] = geteuid();
        entry.value()["bounded_receive_filesystem"] =
            cooperative_->bounded_filesystem();
        if (entry.key() == "JAVA_CPU")
          entry.value()["cpu_engine"] = "async_profiler_ctimer";
      }
    }
    if (!helper_) {
      details["PERF_CPU"]["available"] = perf_available_;
      details["PERF_CPU"]["reason"] = perf_reason_;
      return details;
    }
    std::lock_guard lock(helper_mutex_);
    for (auto &entry : details.items()) {
      if (!helper_kind(entry.key()))
        continue;
      if (helper_capabilities_.contains(entry.key()))
        entry.value() = helper_capabilities_.at(entry.key());
      else {
        entry.value()["available"] = false;
        entry.value()["reason"] = "Dedicated collector helper is unavailable";
      }
      entry.value()["isolation"] = "dedicated_root_helper";
    }
    return details;
  }
  bool refresh_helper() {
    if (!helper_)
      return true;
    json details = json::object();
    bool available = false;
    try {
      details = helper_->capabilities().at("capabilities");
      available = true;
    } catch (...) {
    }
    std::lock_guard lock(helper_mutex_);
    if (helper_capabilities_ != details)
      registration_dirty_ = true;
    helper_capabilities_ = std::move(details);
    return available;
  }
  bool initialize() {
    refresh_helper();
    registration_dirty_ = false;
    dv::AgentInfo info;
    info.set_agent_id(id_);
    char hostname[256]{};
    gethostname(hostname, sizeof(hostname) - 1);
    info.set_hostname(secret("AGENT_HOSTNAME", hostname));
    info.set_ip(secret("AGENT_IP"));
    info.set_version("2.0.0");
    info.set_protocol_version(2);
    struct utsname platform {};
    uname(&platform);
    info.set_platform(std::string(platform.sysname) + " " + platform.release +
                      " " + platform.machine);
    info.set_build_commit(secret("BUILD_COMMIT", "unknown"));
    info.set_os(platform.sysname);
    info.set_arch(platform.machine);
    info.set_kernel(platform.release);
    info.set_certificate_expires_unix_ms(
        unix_ns() / 1000000 +
        certificate_.at("expires_in_seconds").get<std::int64_t>() * 1000);
    auto details = collection_capabilities();
    for (const auto &[kind, status] : details.items())
      if (status.value("available", false))
        info.add_capabilities(kind);
    info.set_capability_details_json(details.dump());
    json tool_versions = json::object();
    for (const auto &[kind, status] : details.items()) {
      (void)kind;
      for (const auto &tool : status.value("tools", json::array()))
        tool_versions[tool.at("name").get<std::string>()] = tool;
    }
    info.set_tool_versions_json(tool_versions.dump());
    info.set_labels_json(json{
        {"environment", "production"},
        {"collection_manifest_version", 1},
        {"collector_isolation", helper_ ? "dedicated_root_helper" : "direct"},
        {"sampling_scope", secret("ALLOW_HOST_PROCESSES", "false") == "true"
                               ? "host-process"
                               : "controlled-workload"}}
                             .dump());
    dv::InitResponse response;
    grpc::Status status;
    for (std::size_t i = 0; i < endpoints_.size(); ++i) {
      const auto index = (endpoint_index_ + i) % endpoints_.size();
      auto endpoint = endpoints_[index];
      grpc::ClientContext context;
      deadline(context);
      status = endpoint->init->Init(&context, info, &response);
      if (status.error_code() == grpc::StatusCode::INVALID_ARGUMENT) {
        auto legacy = info;
        legacy.set_protocol_version(1);
        legacy.clear_capabilities();
        legacy.add_capabilities("PROC_CPU");
        if (details.at("PERF_CPU").value("available", false))
          legacy.add_capabilities("PERF_CPU");
        grpc::ClientContext retry;
        deadline(retry);
        status = endpoint->init->Init(&retry, legacy, &response);
      }
      if (status.ok()) {
        endpoint_index_ = index;
        clients_.store(endpoint);
        break;
      }
    }
    if (!status.ok()) {
      log("drop-agent", "Registration unavailable", "", "REGISTER_FAILED");
      return false;
    }
    if (response.protocol_version() != 1 && response.protocol_version() != 2)
      throw std::runtime_error("Server protocol mismatch");
    negotiated_protocol_ = response.protocol_version();
    metrics_.set("drop_negotiated_protocol_version",
                 response.protocol_version());
    log("drop-agent", "Registered over mTLS");
    record_event("AGENT_REGISTERED",
                 {{"protocol_version", negotiated_protocol_.load()},
                  {"certificate", certificate_}});
    return true;
  }

  json resources(dv::HeartbeatRequest &request) {
    refresh_helper();
    const auto details = collection_capabilities();
    certificate_["expires_in_seconds"] =
        certificate_.at("not_after_unix_seconds").get<std::int64_t>() -
        unix_ns() / 1000000000;
    metrics_.set("drop_certificate_expires_in_seconds",
                 certificate_.at("expires_in_seconds").get<double>());
    std::ifstream cpu("/proc/stat");
    std::string name;
    cpu >> name;
    std::uint64_t value = 0, total = 0, idle = 0;
    for (int i = 0; i < 8; ++i) {
      cpu >> value;
      total += value;
      if (i == 3 || i == 4)
        idle += value;
    }
    double cpu_percent =
        total > previous_total_
            ? 100.0 * (1.0 - static_cast<double>(idle - previous_idle_) /
                                 static_cast<double>(total - previous_total_))
            : 0;
    previous_total_ = total;
    previous_idle_ = idle;
    std::ifstream memory("/proc/meminfo");
    std::string key, unit;
    std::uint64_t amount, memory_total = 0, memory_available = 0;
    while (memory >> key >> amount >> unit) {
      if (key == "MemTotal:")
        memory_total = amount * 1024;
      if (key == "MemAvailable:")
        memory_available = amount * 1024;
    }
    double loads[3]{};
    getloadavg(loads, 3);
    request.set_cpu_percent(std::clamp(cpu_percent, 0.0, 100.0));
    request.set_memory_percent(
        memory_total
            ? 100.0 * static_cast<double>(memory_total - memory_available) /
                  static_cast<double>(memory_total)
            : 0);
    request.set_load_average(loads[0]);
    const auto policy = target_policy(workload_.pid);
    json processes = json::array();
    if (policy.allow_host_processes) {
      for (const auto &entry : fs::directory_iterator("/proc")) {
        const auto pid = entry.path().filename().string();
        if (pid.empty() ||
            pid.find_first_not_of("0123456789") != std::string::npos)
          continue;
        try {
          const auto process = process_identity(std::stoi(pid));
          if (!policy.allowed_uids.contains(process.uid))
            continue;
          processes.push_back({{"pid", process.pid},
                               {"name", fs::path(process.executable)
                                            .filename()
                                            .string()
                                            .substr(0, 128)},
                               {"uid", process.uid},
                               {"start_time_ticks", process.start_time_ticks}});
          if (processes.size() >=
              static_cast<std::size_t>(negotiated_protocol_ >= 2 ? 2000 : 200))
            break;
        } catch (const std::exception &) {
        }
      }
    } else if (workload_.pid > 1) {
      const auto process = process_identity(workload_.pid);
      processes.push_back({{"pid", workload_.pid},
                           {"name", "drop_workload · controlled CPU benchmark"},
                           {"uid", process.uid},
                           {"start_time_ticks", process.start_time_ticks}});
    }
    return {{"processes", processes},
            {"certificate", certificate_},
            {"capability_details", details},
            {"target_policy", target_policy_json(policy)},
            {"memory_total_bytes", memory_total},
            {"memory_used_bytes", memory_total - memory_available},
            {"sampler_status",
             {{"perf",
               {{"available", details.at("PERF_CPU").at("available")},
                {"reason", details.at("PERF_CPU").at("reason")}}},
              {"procfs",
               {{"available", true},
                {"unit", "CPU ticks"},
                {"limitation",
                 "thread-level CPU accounting; no function stacks"}}}}}};
  }

  bool progress(const dv::TaskDesc &task, const std::string &state) {
    dv::ProgressRequest request;
    request.set_agent_id(id_);
    request.set_task_id(task.task_id());
    request.set_attempt_id(task.attempt_id());
    request.set_status(state);
    for (int retry = 0; retry < 3 && alive && !canceled_; ++retry) {
      grpc::ClientContext context;
      deadline(context);
      dv::Ack response;
      auto status =
          clients_.load()->hot->ReportProgress(&context, request, &response);
      if (status.ok() && response.ok())
        return true;
      if (status.error_code() == grpc::StatusCode::CANCELLED) {
        canceled_ = true;
        return false;
      }
      std::this_thread::sleep_for(500ms);
    }
    return false;
  }

  bool notify(const dv::ResultRequest &request, bool *terminal = nullptr) {
    for (unsigned retry = 0; retry < 5 && alive; ++retry) {
      grpc::ClientContext context;
      deadline(context);
      dv::Ack response;
      const auto status =
          clients_.load()->hot->NotifyResult(&context, request, &response);
      if (status.ok() && response.ok()) {
        if (terminal)
          *terminal = response.error_code() == "ATTEMPT_TERMINAL";
        return true;
      }
      if (status.error_code() == grpc::StatusCode::NOT_FOUND) {
        if (terminal)
          *terminal = true;
        return true; // Superseded attempts cannot overwrite current state.
      }
      if (status.error_code() != grpc::StatusCode::UNAVAILABLE &&
          status.error_code() != grpc::StatusCode::DEADLINE_EXCEEDED &&
          status.error_code() != grpc::StatusCode::RESOURCE_EXHAUSTED)
        return false;
      pause_retry(upload_retry_delay_ms(retry, retry_entropy()));
    }
    return false;
  }

  static std::uint32_t retry_entropy() {
    std::uint32_t value = 0;
    if (RAND_bytes(reinterpret_cast<unsigned char *>(&value), sizeof(value)) !=
        1)
      value = static_cast<std::uint32_t>(unix_ns());
    return value;
  }
  void pause_retry(std::uint64_t milliseconds, bool honor_cancel = true) const {
    const auto end = std::chrono::steady_clock::now() +
                     std::chrono::milliseconds(milliseconds);
    while (alive && (!honor_cancel || !canceled_) &&
           std::chrono::steady_clock::now() < end)
      std::this_thread::sleep_for(100ms);
  }
  void persist_result(const fs::path &directory,
                      const dv::ResultRequest &result) {
    atomic_write((directory / "result.pb").string(),
                 result.SerializeAsString());
  }
  fs::path artifact_path(const fs::path &directory,
                         const dv::ArtifactInfo &artifact) const {
    if (artifact.kind() == "LOG" && artifact.name() == "runner.log")
      return directory / "diagnostic.public.log";
    if (!safe_artifact_name(artifact.name()))
      throw std::invalid_argument("Unsafe local artifact filename");
    if (artifact.kind() == "RAW" && cooperative_ &&
        fs::exists(directory / "cooperative-output.json"))
      return cooperative_->raw(directory, artifact.name());
    return directory / artifact.name();
  }
  static bool safe_artifact_name(const std::string &name) {
    return !name.empty() && name.size() <= 128 && name != "." && name != ".." &&
           name.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQR"
                                  "STUVWXYZ0123456789._-") == std::string::npos;
  }
  const dv::UploadTarget *find_grant(const dv::TaskDesc &task,
                                     const dv::ArtifactInfo &artifact) const {
    for (const auto &grant : task.upload_targets())
      if (grant.kind() == artifact.kind() && grant.name() == artifact.name() &&
          grant.object_key() == artifact.object_key())
        return &grant;
    return nullptr;
  }
  static void remove_logs(dv::ResultRequest &result) {
    for (int index = result.artifacts_size() - 1; index >= 0; --index)
      if (result.artifacts(index).kind() == "LOG")
        result.mutable_artifacts()->DeleteSubrange(index, 1);
  }
  void attach_log(const fs::path &directory, const dv::TaskDesc &task,
                  dv::ResultRequest &result) {
    remove_logs(result);
    for (const auto &grant : task.upload_targets()) {
      if (grant.kind() != "LOG" || grant.name() != "runner.log" ||
          grant.max_file_bytes() != 16384)
        continue;
      const auto known = catalog_.entries().contains(task.kind());
      const auto code = result.error_code().size() <= 64 &&
                                result.error_code().find_first_not_of(
                                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789") ==
                                    std::string::npos
                            ? result.error_code()
                            : "RUNNER_FAILED";
      json diagnostic = {
          {"schema_version", 1},
          {"task_id", task.task_id()},
          {"attempt_id", task.attempt_id()},
          {"kind", known ? task.kind() : "UNKNOWN"},
          {"status", result.success() ? "COLLECTED" : "FAILED"},
          {"error_code", code},
          {"exit_code",
           result.exit_code() < 0 ? json(nullptr) : json(result.exit_code())},
          {"message",
           result.success()
               ? "Profiler collection completed"
               : "Profiler collection failed; inspect the fixed error code"},
          {"redaction", "fixed fields only; profiler stderr, argv, URLs and "
                        "RAW content are excluded"}};
      if (!result.runner_metadata_json().empty()) {
        const auto metadata = parse_json(result.runner_metadata_json());
        if (metadata.contains("duration_ms") &&
            metadata["duration_ms"].is_number_integer() &&
            metadata["duration_ms"].get<std::int64_t>() >= 0)
          diagnostic["duration_ms"] = metadata["duration_ms"];
      }
      const auto content = diagnostic.dump() + "\n";
      if (content.size() > 16384)
        throw std::runtime_error("Public diagnostic budget exceeded");
      const auto file = directory / "diagnostic.public.log";
      atomic_write(file.string(), content);
      auto *log_artifact = result.add_artifacts();
      log_artifact->set_name("runner.log");
      log_artifact->set_kind("LOG");
      log_artifact->set_format("log_jsonl");
      log_artifact->set_content_type("text/plain");
      log_artifact->set_object_key(grant.object_key());
      log_artifact->set_size(static_cast<std::int64_t>(content.size()));
      log_artifact->set_sha256(sha256_file(file.string()));
      log_artifact->set_metadata_json(
          "{\"redacted\":true,\"schema_version\":1}");
    }
  }
  UploadReceipt upload_retry(const fs::path &directory,
                             const dv::TaskDesc &task,
                             const dv::ArtifactInfo &artifact,
                             const std::string &url, std::uint64_t maximum,
                             bool renew, unsigned max_attempts = 0) {
    const auto file = artifact_path(directory, artifact);
    if (artifact.size() <= 0 ||
        fs::file_size(file) != static_cast<std::uint64_t>(artifact.size()) ||
        sha256_file(file.string()) != artifact.sha256())
      throw UploadFailure(false);
    const auto marker = directory / ("uploaded-" + artifact.name());
    const auto receipt_file =
        directory / ("receipt-" + artifact.name() + ".json");
    const auto saved_receipt = [&]() -> UploadReceipt {
      if (!fs::is_regular_file(receipt_file))
        return {};
      const auto value = parse_json(read_file(receipt_file.string(), 4096));
      if (value.value("sha256", "") != artifact.sha256())
        throw UploadFailure(false);
      return {value.value("etag", ""),
              value.value("completed_unix_ms", std::int64_t{0})};
    };
    if ((artifact.kind() == "RAW" || artifact.kind() == "BINARY") &&
        fs::is_regular_file(directory / "uploaded"))
      return saved_receipt(); // N-1 journal never repeats already completed
                              // data PUTs.
    if (fs::is_regular_file(marker) &&
        read_file(marker.string(), 128) == artifact.sha256())
      return saved_receipt();
    const auto retry_state = directory / ("retry-" + artifact.name() + ".json");
    unsigned retry = 0;
    if (fs::is_regular_file(retry_state)) {
      const auto saved = parse_json(read_file(retry_state.string(), 4096));
      retry = std::min(saved.value("attempt", 0U), 100000U);
      const auto wait = saved.value("next_retry_unix_ms", std::int64_t{0}) -
                        unix_ns() / 1000000;
      if (wait > 0)
        pause_retry(
            static_cast<std::uint64_t>(std::min(wait, std::int64_t{60000})),
            renew);
    }
    for (unsigned performed = 0; alive && (!renew || !canceled_); ++performed) {
      const auto upload_deadline =
          task.deadline_unix_ms() + (renew ? 0 : 120000);
      if (task.deadline_unix_ms() > 0 && unix_ns() / 1000000 >= upload_deadline)
        throw UploadFailure(false);
      unsigned retry_after = 0;
      try {
        if (renew && !progress(task, "UPLOADING"))
          throw UploadFailure(!canceled_);
        const auto receipt =
            upload_file(url, file.string(), artifact.content_type(), maximum);
        atomic_write(receipt_file.string(),
                     json{{"sha256", artifact.sha256()},
                          {"etag", receipt.etag},
                          {"completed_unix_ms", receipt.completed_unix_ms}}
                         .dump());
        atomic_write(marker.string(), artifact.sha256());
        metrics_.add("drop_upload_bytes_total",
                     static_cast<double>(artifact.size()));
        return receipt;
      } catch (const UploadFailure &error) {
        metrics_.add("drop_upload_errors_total");
        if (!error.retryable || (max_attempts && performed + 1 >= max_attempts))
          throw;
        retry_after = error.retry_after_seconds;
      } catch (const std::exception &) {
        throw UploadFailure(
            false); // Local file/configuration failures are permanent.
      }
      const auto delay = std::max<std::uint64_t>(
          upload_retry_delay_ms(retry, retry_entropy()),
          static_cast<std::uint64_t>(retry_after) * 1000);
      atomic_write(
          retry_state.string(),
          json{{"attempt", ++retry},
               {"next_retry_unix_ms",
                unix_ns() / 1000000 + static_cast<std::int64_t>(delay)}}
              .dump());
      pause_retry(delay, renew);
    }
    throw UploadFailure(
        true); // Shutdown keeps the successful collection journal.
  }
  static void apply_receipt(dv::ArtifactInfo &artifact,
                            const UploadReceipt &receipt) {
    auto metadata = artifact.metadata_json().empty()
                        ? json::object()
                        : parse_json(artifact.metadata_json());
    metadata["put_receipt"] = {
        {"etag", receipt.etag.empty() ? json(nullptr) : json(receipt.etag)},
        {"completed_unix_ms", receipt.completed_unix_ms > 0
                                  ? json(receipt.completed_unix_ms)
                                  : json(nullptr)},
        {"semantics", "opaque HTTP ETag; SHA256 remains the integrity digest"}};
    if (metadata.dump().size() > 32768)
      throw UploadFailure(false);
    artifact.set_metadata_json(metadata.dump());
  }
  void attach_manifest(const fs::path &directory, const dv::TaskDesc &task,
                       dv::ResultRequest &result) {
    const dv::UploadTarget *grant = nullptr;
    for (const auto &target : task.upload_targets())
      if (target.kind() == "MANIFEST" &&
          target.name() == "collection-manifest.json" &&
          target.max_file_bytes() == 65536)
        grant = &target;
    if (!grant)
      return; // N-1 attempts never received this feature grant.
    for (int i = result.artifacts_size() - 1; i >= 0; --i)
      if (result.artifacts(i).kind() == "MANIFEST")
        result.mutable_artifacts()->DeleteSubrange(i, 1);
    const auto file = directory / "collection-manifest.json";
    if (fs::is_symlink(file))
      throw UploadFailure(false);
    if (!fs::is_regular_file(file)) {
      const auto metadata = parse_json(result.runner_metadata_json());
      const auto provenance = metadata.at("collection_provenance");
      CollectionManifest context;
      context.task_id = task.task_id();
      context.attempt_id = task.attempt_id();
      context.kind = task.kind();
      context.agent_id = provenance.at("agent_id");
      context.agent_version = provenance.at("agent_version");
      context.build_commit = provenance.at("build_commit");
      context.started_ms = metadata.at("collection_started_unix_ms");
      context.finished_ms = metadata.at("collection_finished_unix_ms");
      context.tools = provenance.at("tools");
      context.parameters = task.extra_parameters_json().empty()
                               ? json::object()
                               : parse_json(task.extra_parameters_json());
      context.parameters["pid"] = task.parameters().pid();
      context.parameters["duration_seconds"] =
          task.parameters().duration_seconds();
      context.parameters["frequency_hz"] = task.parameters().frequency_hz();
      context.parameters["scope"] = task.parameters().scope();
      context.parameters["target_type"] = task.target().type();
      const auto append = [&](const dv::ArtifactInfo &artifact) {
        const auto details = parse_json(artifact.metadata_json());
        const auto receipt = details.at("put_receipt");
        context.uploaded_ms =
            std::max(context.uploaded_ms,
                     receipt.at("completed_unix_ms").get<std::int64_t>());
        context.files.push_back({{"kind", artifact.kind()},
                                 {"name", artifact.name()},
                                 {"object_key", artifact.object_key()},
                                 {"size", artifact.size()},
                                 {"sha256", artifact.sha256()},
                                 {"etag", receipt.at("etag")}});
      };
      append(result.artifact());
      for (const auto &artifact : result.artifacts())
        if (artifact.kind() == "BINARY")
          append(artifact);
      atomic_write(file.string(), build_collection_manifest(context));
    } else {
      const auto saved = parse_json(read_file(file.string(), 65537));
      if (fs::file_size(file) > 65536 ||
          saved.value("task_id", 0LL) != task.task_id() ||
          saved.value("attempt_id", 0LL) != task.attempt_id() ||
          saved.value("task_kind", "") != task.kind())
        throw UploadFailure(false);
    }
    auto *artifact = result.add_artifacts();
    artifact->set_kind("MANIFEST");
    artifact->set_name("collection-manifest.json");
    artifact->set_format("collection_manifest_v1");
    artifact->set_content_type("application/json");
    artifact->set_object_key(grant->object_key());
    artifact->set_size(static_cast<std::int64_t>(fs::file_size(file)));
    artifact->set_sha256(sha256_file(file.string()));
    artifact->set_metadata_json("{\"schema_version\":1}");
    persist_result(
        directory,
        result); // Immutable manifest bytes survive any interrupted PUT.
    const auto receipt =
        upload_retry(directory, task, *artifact, grant->put_url(), 65536, true);
    apply_receipt(*artifact, receipt);
  }
  void remove_attempt(const fs::path &directory) const {
    atomic_write((directory / "cleanup.json").string(),
                 "{\"terminal_acknowledged\":true}");
    if (cooperative_)
      cooperative_->remove(directory);
    fs::remove_all(directory);
  }
  bool has_raw(const fs::path &directory, const dv::TaskDesc &task,
               const dv::ResultRequest &result) const {
    if (result.has_artifact() && safe_artifact_name(result.artifact().name()) &&
        fs::is_regular_file(artifact_path(directory, result.artifact())))
      return true;
    const auto &spec = catalog_.get(task.kind());
    if (cooperative_ && fs::exists(directory / "cooperative-output.json") &&
        fs::is_regular_file(cooperative_->raw(
            directory, spec.at("raw_name").get<std::string>())))
      return true;
    if (fs::is_regular_file(directory / spec.at("raw_name").get<std::string>()))
      return true;
    return task.kind() == "PERF_CPU" &&
           fs::is_regular_file(directory / "profile.folded");
  }
  void retain_terminal(const fs::path &directory) const {
    if (fs::is_regular_file(directory / "terminal.json"))
      return;
    atomic_write(
        (directory / "terminal.json").string(),
        json{{"expires_unix_ms",
              unix_ns() / 1000000 +
                  static_cast<std::int64_t>(retention_seconds_) * 1000}}
            .dump());
  }
  std::uint64_t journal_bytes() const {
    std::uint64_t bytes = cooperative_
                              ? cooperative_->bytes(recovery_disk_bytes_)
                              : 0,
                  files = 0;
    for (const auto &entry : fs::recursive_directory_iterator(directory_)) {
      if (++files > 100000)
        throw std::runtime_error("Recovery journal entry budget exceeded");
      if (entry.is_regular_file() && !entry.is_symlink()) {
        const auto size = entry.file_size();
        if (size > recovery_disk_bytes_ || bytes > recovery_disk_bytes_ - size)
          return recovery_disk_bytes_;
        bytes += size;
      }
    }
    return bytes;
  }
  void complete(const fs::path &directory, const dv::TaskDesc &task,
                dv::ResultRequest result) {
    const auto upload_started = unix_ns();
    if (!result.success())
      result.clear_artifacts();
    persist_result(directory, result);
    if (result.success()) {
      try {
        const auto raw_receipt = upload_retry(
            directory, task, result.artifact(), task.raw_upload_url(),
            task.max_file_bytes() > 0
                ? static_cast<std::uint64_t>(task.max_file_bytes())
                : 33554432ULL,
            true);
        apply_receipt(*result.mutable_artifact(), raw_receipt);
        for (int index = 0; index < result.artifacts_size(); ++index) {
          auto &artifact = *result.mutable_artifacts(index);
          if (artifact.kind() == "LOG" || artifact.kind() == "MANIFEST")
            continue;
          const auto *grant = find_grant(task, artifact);
          if (!grant)
            throw UploadFailure(false);
          const auto receipt = upload_retry(
              directory, task, artifact, grant->put_url(),
              static_cast<std::uint64_t>(grant->max_file_bytes()), true);
          apply_receipt(artifact, receipt);
        }
        attach_manifest(directory, task, result);
        log("drop-agent", "Artifact upload completed",
            std::to_string(task.task_id()), "",
            task_context(task, "upload", (unix_ns() - upload_started) / 1e6));
      } catch (const std::exception &) {
        if (!alive)
          return;
        result.set_success(false);
        result.set_error_code(canceled_ ? "TASK_CANCELED"
                              : task.deadline_unix_ms() > 0 &&
                                      unix_ns() / 1000000 >=
                                          task.deadline_unix_ms()
                                  ? "TASK_TIMEOUT"
                                  : "ARTIFACT_UPLOAD_FAILED");
        result.set_error_message(
            "Bounded artifact upload did not complete; RAW retained for the "
            "configured recovery period");
        // Failed requests may only commit a safe LOG, never an incomplete
        // RAW/BINARY.
        result.clear_artifacts();
      }
    }
    attach_log(directory, task, result);
    bool log_pending = false;
    std::optional<dv::ArtifactInfo> selected_log;
    for (const auto &artifact : result.artifacts())
      if (artifact.kind() == "LOG") {
        selected_log = artifact;
        break;
      }
    if (selected_log) {
      const auto diagnostic = *selected_log;
      const auto *grant = find_grant(task, diagnostic);
      try {
        if (!grant)
          throw UploadFailure(false);
        upload_retry(directory, task, diagnostic, grant->put_url(), 16384,
                     false, 3);
      } catch (const UploadFailure &error) {
        if (!alive)
          return;
        remove_logs(result);
        log_pending = error.retryable;
        metrics_.add("drop_log_upload_errors_total");
      } catch (const std::exception &) {
        if (!alive)
          return;
        remove_logs(result);
        metrics_.add("drop_log_upload_errors_total");
      }
    }
    persist_result(directory, result);
    bool terminal = false;
    if (notify(result, &terminal)) {
      if (log_pending) {
        if (!fs::is_regular_file(directory / "log-pending.json"))
          atomic_write(
              (directory / "log-pending.json").string(),
              json{{"expires_unix_ms", unix_ns() / 1000000 + 120000}}.dump());
      } else {
        fs::remove(directory / "log-pending.json");
      }
      if (log_pending && result.success() && !terminal) {
        // Keep only a short replay journal for the already committed RAW and
        // retry its missing LOG; uploaded SHA markers prevent another RAW PUT.
      } else if ((result.success() && !terminal) ||
                 (!has_raw(directory, task, result) && !log_pending))
        remove_attempt(directory);
      else
        retain_terminal(directory);
    } else {
      log("drop-agent", "Result journal retained for reconnect recovery",
          std::to_string(task.task_id()), "RESULT_REPORT_PENDING",
          task_context(task, "notify"));
    }
  }
  void recover() {
    if (worker_.joinable())
      worker_.join();
    if (cooperative_) {
      try {
        metrics_.add("drop_cooperative_orphans_removed_total",
                     cooperative_->sweep(directory_, retention_seconds_));
      } catch (...) {
        log("drop-agent", "Cooperative retention scan deferred", "",
            "JOURNAL_RECOVERY_FAILED");
      }
    }
    unsigned visited = 0;
    for (const auto &entry : fs::directory_iterator(directory_)) {
      if (++visited > 10000)
        break;
      if (!entry.is_directory() || entry.is_symlink() ||
          entry.path().filename().string().rfind("attempt-", 0) != 0 ||
          fs::canonical(entry.path()).parent_path() !=
              fs::canonical(directory_))
        continue;
      try {
        if (fs::is_regular_file(entry.path() / "cleanup.json")) {
          const auto cleanup = parse_json(
              read_file((entry.path() / "cleanup.json").string(), 256));
          if (cleanup == json{{"terminal_acknowledged", true}})
            remove_attempt(entry.path());
          continue;
        }
        bool log_pending =
            fs::is_regular_file(entry.path() / "log-pending.json");
        if (log_pending) {
          const auto expiry =
              parse_json(
                  read_file((entry.path() / "log-pending.json").string(), 4096))
                  .at("expires_unix_ms")
                  .get<std::int64_t>();
          if (expiry <= unix_ns() / 1000000) {
            fs::remove(entry.path() / "log-pending.json");
            log_pending = false;
            if (!fs::exists(entry.path() / "terminal.json")) {
              remove_attempt(entry.path());
              continue;
            }
          }
        }
        if (!log_pending &&
            fs::is_regular_file(entry.path() / "terminal.json")) {
          const auto expiry =
              parse_json(
                  read_file((entry.path() / "terminal.json").string(), 4096))
                  .at("expires_unix_ms")
                  .get<std::int64_t>();
          if (expiry <= unix_ns() / 1000000)
            remove_attempt(entry.path());
          continue;
        }
        dv::TaskDesc task;
        if (!task.ParseFromString(
                read_file((entry.path() / "task.pb").string())) ||
            task.target_agent_id() != id_ || task.attempt_id() <= 0)
          continue;
        dv::ResultRequest result;
        if (fs::exists(entry.path() / "result.pb")) {
          if (!result.ParseFromString(
                  read_file((entry.path() / "result.pb").string())))
            continue;
        } else {
          result.set_agent_id(id_);
          result.set_task_id(task.task_id());
          result.set_attempt_id(task.attempt_id());
          result.set_success(false);
          result.set_exit_code(-1);
          result.set_error_code("AGENT_RESTARTED");
          result.set_error_message("Agent restarted during collection; no "
                                   "duplicate sampling was started");
        }
        canceled_ = false;
        attempt_ = task.attempt_id();
        const auto directory = entry.path();
        worker_ = std::jthread([this, directory, task, result] {
          try {
            auto recovered = result;
            if (!fs::is_regular_file(directory / "result.pb"))
              resume_helper(directory, task, recovered);
            else
              acknowledge_helper(directory);
            complete(directory, task, recovered);
          } catch (...) {
            log("drop-agent", "Recovery journal retained",
                std::to_string(task.task_id()), "JOURNAL_RECOVERY_FAILED");
          }
          attempt_ = 0;
        });
        return; // Recovery runs on a worker; heartbeat/lease renewal never
                // pauses.
      } catch (...) {
        log("drop-agent", "Could not recover local attempt journal", "",
            "JOURNAL_RECOVERY_FAILED");
      }
    }
  }

  void collected_result(const fs::path &directory, const dv::TaskDesc &task,
                        const ProcessIdentity &identity,
                        const RunnerOutput &output, dv::ResultRequest &result,
                        bool from_helper) {
    const auto file_size = fs::file_size(output.path);
    if (!file_size ||
        file_size > static_cast<std::uint64_t>(task.max_file_bytes() > 0
                                                   ? task.max_file_bytes()
                                                   : 33554432))
      throw std::runtime_error("Raw artifact exceeds configured budget");
    if (cooperative_ && fs::exists(directory / "cooperative-output.json"))
      cooperative_->sync(directory, output.name,
                         static_cast<std::uint64_t>(task.max_file_bytes() > 0
                                                        ? task.max_file_bytes()
                                                        : 33554432));
    auto *artifact = result.mutable_artifact();
    artifact->set_object_key(task.raw_object_key());
    artifact->set_size(static_cast<std::int64_t>(file_size));
    artifact->set_sha256(sha256_file(output.path.string()));
    artifact->set_content_type(output.content_type);
    artifact->set_name(output.name);
    artifact->set_format(output.format);
    artifact->set_kind("RAW");
    artifact->set_metadata_json(output.metadata.dump());
    result.set_runner_metadata_json(output.metadata.dump());
    for (const auto &grant : task.upload_targets())
      if (grant.kind() == "BINARY" && grant.name() == "target.elf") {
        const auto source = "/proc/" + std::to_string(identity.pid) + "/exe";
        if (identity.pid <= 1 ||
            (!from_helper &&
             fs::file_size(source) >
                 static_cast<std::uint64_t>(grant.max_file_bytes())))
          throw std::runtime_error(
              "Target executable exceeds binary artifact budget");
        const auto binary = directory / "target.elf";
        if (!from_helper)
          fs::copy_file(source, binary, fs::copy_options::overwrite_existing);
        std::ifstream header(binary, std::ios::binary);
        std::array<char, 4> magic{};
        header.read(magic.data(), 4);
        if (std::string(magic.data(), magic.size()) != std::string("\x7f"
                                                                   "ELF",
                                                                   4))
          throw std::runtime_error("Target binary is not ELF");
        if (!fs::is_regular_file(binary) || fs::is_symlink(binary) ||
            fs::file_size(binary) >
                static_cast<std::uint64_t>(grant.max_file_bytes()))
          throw std::runtime_error("Invalid bounded binary artifact");
        auto *extra = result.add_artifacts();
        extra->set_object_key(grant.object_key());
        extra->set_name("target.elf");
        extra->set_kind("BINARY");
        extra->set_format("elf");
        extra->set_content_type("application/octet-stream");
        extra->set_size(static_cast<std::int64_t>(fs::file_size(binary)));
        extra->set_sha256(sha256_file(binary.string()));
        extra->set_metadata_json(
            json{{"target_pid", identity.pid},
                 {"start_time_ticks", identity.start_time_ticks}}
                .dump());
      }
    result.set_success(true);
    result.set_exit_code(0);
  }
  static std::string helper_token() {
    std::array<unsigned char, 32> bytes{};
    if (RAND_bytes(bytes.data(), bytes.size()) != 1)
      throw std::runtime_error("Cannot generate helper operation identity");
    std::string token;
    static constexpr char alphabet[] = "0123456789abcdef";
    for (auto byte : bytes) {
      token += alphabet[byte >> 4];
      token += alphabet[byte & 15];
    }
    return token;
  }
  void acknowledge_helper(const fs::path &directory) {
    if (!helper_ || !fs::is_regular_file(directory / "helper-request.json"))
      return;
    try {
      const auto request = parse_json(
          read_file((directory / "helper-request.json").string(), 65536));
      helper_->acknowledge(request.at("token"));
    } catch (...) {
      // The durable Agent result is authoritative; helper TTL remains bounded.
      metrics_.add("drop_helper_ack_errors_total");
    }
  }
  void resume_helper(const fs::path &directory, const dv::TaskDesc &task,
                     dv::ResultRequest &result) {
    if (!helper_ || !fs::is_regular_file(directory / "helper-request.json"))
      return;
    try {
      auto request = decode_helper_request(
          parse_json(
              read_file((directory / "helper-request.json").string(), 65536)),
          catalog_);
      if (request.context.kind != task.kind())
        throw HelperFailure("INVALID_HELPER_REQUEST");
      request.context.directory = directory;
      request.context.canceled = [this] { return canceled_.load() || !alive; };
      RunnerOutput output;
      for (unsigned retry = 0;; ++retry) {
        try {
          output = helper_->collect(request, true);
          break;
        } catch (const HelperFailure &error) {
          if ((error.error_code != "HELPER_BUSY" &&
               error.error_code != "HELPER_NOT_READY") ||
              retry >= 20)
            throw;
          pause_retry(100);
        }
      }
      decorate_output(output);
      collected_result(directory, task, request.context.target, output, result,
                       true);
      persist_result(directory, result);
      acknowledge_helper(directory);
    } catch (const std::exception &error) {
      const auto *helper_error = dynamic_cast<const HelperFailure *>(&error);
      result.set_success(false);
      result.set_error_code(helper_error ? helper_error->error_code
                                         : "HELPER_PROTOCOL_ERROR");
      result.set_error_message(
          "Local helper recovery did not replay a completed capture; no "
          "duplicate sampling was started");
    }
  }
  void decorate_output(RunnerOutput &output) const {
    json captured_tools = json::array();
    for (const auto &tool : output.metadata.value("tool_probes", json::array()))
      captured_tools.push_back({{"name", tool.at("name")},
                                {"version", tool.at("version")},
                                {"sha256", tool.at("sha256")}});
    output.metadata["collection_provenance"] = {
        {"agent_id", id_},
        {"agent_version", "2.0.0"},
        {"build_commit", secret("BUILD_COMMIT", "unknown")},
        {"tools", captured_tools},
        {"collector_child_cpu_affinity", collector_affinity_}};
  }

  void execute_task(const dv::TaskDesc &task) {
    const auto started_ns = unix_ns();
    record_event("RUNNER_ACCEPTED", {{"task_id", task.task_id()},
                                     {"attempt_id", task.attempt_id()},
                                     {"kind", task.kind()}});
    const fs::path directory =
        fs::path(directory_) / ("attempt-" + std::to_string(task.attempt_id()));
    dv::ResultRequest result;
    result.set_agent_id(id_);
    result.set_task_id(task.task_id());
    result.set_attempt_id(task.attempt_id());
    result.set_exit_code(-1);
    std::string error_code = "RUNNER_FAILED";
    std::int64_t collection_started_ms = 0;
    try {
      if (task.target_agent_id() != id_ || task.task_id() <= 0 ||
          task.attempt_id() <= 0)
        throw std::invalid_argument("Task identity mismatch");
      fs::create_directory(directory);
      chmod(directory.c_str(), 0700);
      if (fs::is_symlink(directory) ||
          fs::canonical(directory).parent_path() != fs::canonical(directory_))
        throw std::runtime_error("Task directory escaped configured workspace");
      atomic_write((directory / "task.pb").string(), task.SerializeAsString());
      const auto &spec = catalog_.get(task.kind());
      json full = {{"pid", task.parameters().pid()},
                   {"duration_seconds", task.parameters().duration_seconds()},
                   {"frequency_hz", task.parameters().frequency_hz()},
                   {"scope", task.parameters().scope().empty()
                                 ? "process"
                                 : task.parameters().scope()}};
      full["options"] = task.extra_parameters_json().empty()
                            ? json::object()
                            : parse_json(task.extra_parameters_json());
      if (task.has_target())
        full["target"] = {
            {"type",
             task.target().type().empty() ? "process" : task.target().type()},
            {"pid", task.target().pid()},
            {"start_time_ticks", task.target().start_time_ticks()},
            {"container_id", task.target().container_id()},
            {"runtime", task.target().runtime().empty()
                            ? "docker"
                            : task.target().runtime()},
            {"container_pid", task.target().container_pid()},
            {"expected_uid", task.target().expected_uid()},
            {"check_uid", task.target().check_uid()}};
      // Omit irrelevant empty fields before applying the authoritative schema.
      if (full.contains("target")) {
        auto &t = full["target"];
        if (t["pid"] == 0)
          t.erase("pid");
        if (t["container_pid"] == 0)
          t.erase("container_pid");
        if (t["container_id"] == "")
          t.erase("container_id");
      }
      if (full["pid"] == 0)
        full.erase("pid");
      if (negotiated_protocol_ == 1) {
        if (task.kind() != "PERF_CPU" && task.kind() != "PROC_CPU")
          throw std::invalid_argument(
              "Negotiated protocol1 permits PERF_CPU/PROC_CPU only");
        if (!full.contains("pid") && !full.contains("target"))
          full["pid"] = workload_.pid;
      }
      error_code = "INVALID_PARAMETERS";
      catalog_.validate(task.kind(), full);
      error_code = "TARGET_VALIDATION_FAILED";
      const auto identity =
          resolve_target(target_from_json(full), target_policy(workload_.pid),
                         directory.string());
      SamplingParameters parameters{
          identity.pid, task.parameters().duration_seconds(),
          task.parameters().frequency_hz(), "process"};
      if (task.kind() == "PERF_CPU" || task.kind() == "PROC_CPU")
        validate(parameters);
      const auto start_time = identity.start_time_ticks;
      const auto maximum = std::min<std::uint64_t>(
          static_cast<std::uint64_t>(
              task.max_file_bytes() > 0 ? task.max_file_bytes() : 33554432),
          spec.at("max_file_bytes").get<std::uint64_t>());
      if (maximum < 1024 || maximum > 512ULL * 1024 * 1024)
        throw std::invalid_argument("Invalid artifact budget");
      std::uint64_t scratch_budget = maximum + 1024 * 1024;
      for (const auto &grant : task.upload_targets()) {
        if (grant.kind() != "BINARY")
          continue;
        if (grant.max_file_bytes() <= 0 ||
            grant.max_file_bytes() > 512LL * 1024 * 1024)
          throw std::invalid_argument("Invalid auxiliary file budget");
        scratch_budget += static_cast<std::uint64_t>(grant.max_file_bytes());
      }
      if (scratch_budget > recovery_disk_bytes_ ||
          journal_bytes() > recovery_disk_bytes_ - scratch_budget ||
          fs::space(directory).available <
              scratch_budget + 256ULL * 1024 * 1024) {
        error_code = "DISK_BUDGET_EXCEEDED";
        throw std::runtime_error(
            "Insufficient scratch reserve for this task budget");
      }
      RunnerContext context;
      context.kind = task.kind();
      context.protocol_version = negotiated_protocol_;
      context.target = identity;
      context.parameters = full;
      context.options = full["options"];
      context.directory = directory;
      context.duration_seconds = parameters.duration_seconds;
      context.frequency_hz = parameters.frequency_hz;
      context.max_file_bytes = maximum;
      context.canceled = [&] {
        return canceled_.load() || !alive ||
               unix_ns() / 1000000 >= task.deadline_unix_ms();
      };
      context.limits.file_bytes = maximum;
      if (cooperative_ && cooperative_agent_kind(task.kind())) {
        if (identity.uid != geteuid()) {
          error_code = "CAPABILITY_UNAVAILABLE";
          throw std::runtime_error(
              "Cooperative profiler requires a target with the Agent UID");
        }
        context.output_directory =
            cooperative_->prepare(directory, task.kind(), task.attempt_id());
        if (fs::space(context.output_directory).available <
            maximum + 256ULL * 1024 * 1024) {
          error_code = "DISK_BUDGET_EXCEEDED";
          throw std::runtime_error(
              "Insufficient bounded cooperative RAW reserve");
        }
        context.java_cpu_timer = true;
      }
      context.limits.cpu_affinity = collector_affinity_;
      context.limits.address_space_bytes =
          task.budget().max_address_space_bytes() > 0
              ? static_cast<std::uint64_t>(
                    task.budget().max_address_space_bytes())
              : 8ULL * 1024 * 1024 * 1024;
      context.limits.cpu_seconds =
          task.budget().max_cpu_seconds() > 0
              ? static_cast<unsigned int>(task.budget().max_cpu_seconds())
              : 120;
      context.limits.processes =
          task.budget().max_processes() > 0
              ? static_cast<unsigned int>(task.budget().max_processes())
              : 32;
      if (context.limits.address_space_bytes > 8ULL * 1024 * 1024 * 1024 ||
          context.limits.cpu_seconds > 300 || context.limits.processes > 64)
        throw std::invalid_argument("Execution budget exceeds Agent policy");
      RunnerOutput output{directory / spec.at("raw_name").get<std::string>(),
                          spec.at("raw_name"),
                          spec.at("input_format"),
                          spec.at("raw_content_type"),
                          {{"kind", task.kind()},
                           {"target_pid", identity.pid},
                           {"start_time_ticks", start_time}}};
      const auto now_ms =
          std::chrono::duration_cast<std::chrono::milliseconds>(
              std::chrono::system_clock::now().time_since_epoch())
              .count();
      if (task.deadline_unix_ms() <=
          now_ms + parameters.duration_seconds * 1000) {
        error_code = "TASK_TIMEOUT";
        throw std::runtime_error("Insufficient collection deadline");
      }
      error_code = "RUNNER_FAILED";
      if (!progress(task, "RUNNING")) {
        error_code = canceled_ ? "TASK_CANCELED" : "CONTROL_UNAVAILABLE";
        throw std::runtime_error("Runner start was not acknowledged");
      }
      if (!helper_ && task.kind() == "PERF_CPU" && !perf_available_) {
        error_code = "PERF_UNAVAILABLE";
        throw std::runtime_error(perf_reason_);
      }
      if (!collection_capabilities()
               .at(task.kind())
               .value("available", false)) {
        error_code = "CAPABILITY_UNAVAILABLE";
        throw std::runtime_error(
            "Required profiler tool or permission unavailable");
      }
      collection_started_ms = unix_ns() / 1000000;
      if (helper_ && helper_kind(task.kind())) {
        HelperRequest request;
        request.token = helper_token();
        request.context = context;
        request.controlled_pid = workload_.pid;
        for (const auto &grant : task.upload_targets())
          if (grant.kind() == "BINARY" && grant.name() == "target.elf")
            request.binary_max = grant.max_file_bytes();
        atomic_write((directory / "helper-request.json").string(),
                     encode_helper_request(request).dump());
        try {
          output = helper_->collect(request);
        } catch (const HelperFailure &failure) {
          if (failure.error_code != "HELPER_UNAVAILABLE")
            throw;
          // The helper may already have durably completed its capture. Replay
          // that exact token; never issue a second collect after uncertain IPC.
          output = helper_->collect(request, true);
        }
      } else {
        output = registry_->collect(context);
        output.metadata["collection_started_unix_ms"] = collection_started_ms;
        output.metadata["collection_finished_unix_ms"] = unix_ns() / 1000000;
        if (cooperative_ && cooperative_agent_kind(task.kind()))
          output.metadata["collection_isolation"] = {
              {"agent_uid", geteuid()},
              {"execution_model", "same_uid_agent_cooperation"},
              {"bounded_filesystem", cooperative_->bounded_filesystem()}};
      }
      decorate_output(output);
      result.set_exit_code(0);
      if (identity.pid > 1 &&
          !same_identity(identity, process_identity(identity.pid))) {
        error_code = "TARGET_CHANGED";
        throw std::runtime_error("Target process was replaced");
      }
      auto file_size = fs::file_size(output.path);
      if (!file_size || file_size > maximum) {
        error_code = "ARTIFACT_TOO_LARGE";
        throw std::runtime_error("Raw artifact exceeds configured budget");
      }
      if (!progress(task, "UPLOADING")) {
        error_code = canceled_ ? "TASK_CANCELED" : "CONTROL_UNAVAILABLE";
        throw std::runtime_error("Upload transition was not acknowledged");
      }
      fs::remove(directory / "perf.data");
      fs::remove(directory / "perf.script");
      if (fs::exists(directory / "runner.log") &&
          fs::file_size(directory / "runner.log") > 16384)
        fs::resize_file(directory / "runner.log", 16384);
      collected_result(directory, task, identity, output, result,
                       bool(helper_ && helper_kind(task.kind())));
      persist_result(directory, result);
      acknowledge_helper(directory);
    } catch (const std::exception &error) {
      if (const auto *helper_error =
              dynamic_cast<const HelperFailure *>(&error))
        error_code = helper_error->error_code;
      if (const auto *execution =
              dynamic_cast<const ProfilerExecutionFailure *>(&error)) {
        result.set_exit_code(execution->exit_code);
        error_code = execution->error_code;
      }
      if (canceled_ || !alive)
        error_code = "TASK_CANCELED";
      else if (task.deadline_unix_ms() > 0 &&
               unix_ns() / 1000000 >= task.deadline_unix_ms())
        error_code = "TASK_TIMEOUT";
      result.set_success(false);
      result.set_error_code(error_code);
      result.set_error_message(std::string(error.what()).substr(0, 1024));
      log("drop-agent", "Collection failed", std::to_string(task.task_id()),
          error_code,
          task_context(task, "collection", (unix_ns() - started_ns) / 1e6));
    }
    metrics_.duration(task.kind(), (unix_ns() - started_ns) / 1e9,
                      result.success());
    if (result.success())
      log("drop-agent", "Collection completed", std::to_string(task.task_id()),
          "", task_context(task, "collection", (unix_ns() - started_ns) / 1e6));
    record_event("RUNNER_FINISHED", {{"task_id", task.task_id()},
                                     {"attempt_id", task.attempt_id()},
                                     {"kind", task.kind()},
                                     {"success", result.success()},
                                     {"error_code", result.error_code()}});
    metrics_.trace("collect." + task.kind(), task.request_id(), task.task_id(),
                   started_ns, result.success(), task.traceparent());
    try {
      if (fs::exists(directory))
        complete(directory, task, result);
      else
        notify(result);
    } catch (...) {
      log("drop-agent", "Result could not be finalized; journal retained",
          std::to_string(task.task_id()), "FINALIZE_FAILED",
          task_context(task, "finalize"));
    }
  }

  Workload workload_;
  std::string id_, directory_, perf_, perf_reason_;
  Catalog catalog_;
  std::unique_ptr<RunnerRegistry> registry_;
  std::unique_ptr<HelperClient> helper_;
  std::unique_ptr<CooperativeFiles> cooperative_;
  mutable std::mutex helper_mutex_;
  json helper_capabilities_ = json::object();
  bool registration_dirty_ = false;
  Telemetry metrics_{"drop-agent", 9465};
  json certificate_ = json::object();
  std::int64_t last_heartbeat_ = 0;
  bool perf_available_ = false;
  std::uint64_t previous_total_ = 0, previous_idle_ = 0;
  std::vector<unsigned> collector_affinity_;
  std::uint64_t retention_seconds_ = 86400;
  std::uint64_t recovery_disk_bytes_ = 1073741824;
  int recovery_ticks_ = 0;
  int heartbeat_failures_ = 0;
  std::atomic<int> negotiated_protocol_{2};
  std::atomic<std::int64_t> attempt_{0};
  std::atomic<bool> canceled_{false};
  std::vector<std::shared_ptr<Clients>> endpoints_;
  std::atomic<std::shared_ptr<Clients>> clients_;
  std::size_t endpoint_index_ = 0;
  std::mutex events_mutex_;
  std::deque<dv::AgentEvent> events_;
  std::atomic<std::uint64_t> event_sequence_{0};
  std::jthread worker_;
};
} // namespace dropcore

int main(int argc, char **argv) {
  std::signal(SIGTERM, dropcore::stop);
  std::signal(SIGINT, dropcore::stop);
  curl_global_init(CURL_GLOBAL_DEFAULT);
  try {
    dropcore::initialize_configuration(argc, argv);
    dropcore::Agent agent;
    dropcore::log_configuration("drop-agent");
    agent.run();
  } catch (const std::exception &) {
    dropcore::log("drop-agent", "Startup failed; check TLS files and workspace",
                  "", "CONFIGURATION_ERROR");
    return 1;
  }
  curl_global_cleanup();
}
