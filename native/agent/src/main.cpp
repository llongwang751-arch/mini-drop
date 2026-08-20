#include <grpcpp/grpcpp.h>

#include "healthcheck.grpc.pb.h"
#include "hotmethod.grpc.pb.h"
#include "init.grpc.pb.h"
#include "config.h"
#include "task.h"
#include "process_runner.h"
#include "artifact_uploader.h"
#include "collector_registry.h"
#include "result_outbox.h"

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
using mini_drop_native::upload_artifact;
using mini_drop_native::CollectorRegistry;
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

TaskResult execute_task(
    const Config& config,
    const Task& task,
    std::atomic<bool>& cancel_requested) {
  static const CollectorRegistry registry = make_default_collector_registry();
  const auto* collector = registry.find(task.profiler_type);
  if (collector == nullptr) {
    TaskResult result;
    result.task_id = task.id;
    result.error = "native C++ Agent has no collector plugin for profiler_type=" +
        std::to_string(task.profiler_type);
    return result;
  }
  return collector->collect(config, task, g_stop, cancel_requested);
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
    if (!cos.endpoint().empty()) config.minio_endpoint = cos.endpoint();
    if (!cos.access_key().empty()) config.minio_access = cos.access_key();
    if (!cos.secret_key().empty()) config.minio_secret = cos.secret_key();
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
    const std::string& active_task_id) {
  mini_drop::HealthCheckRequest request;
  request.set_agent_id(config.agent_id);
  request.set_hostname(hostname());
  request.set_ip_addr(config.agent_ip);
  request.set_agent_version("0.3.0-native-cpp");
  request.set_busy(busy);
  request.set_active_task_id(active_task_id);

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
  task.pid = desc.sample_argv().pid();
  task.hz = static_cast<int>(desc.sample_argv().hz());
  task.duration = static_cast<int>(desc.sample_argv().duration());
  task.timeout = desc.timeout_sec() > 0
                     ? static_cast<int>(desc.timeout_sec())
                     : task.duration + 30;
  task.callgraph = desc.sample_argv().callgraph();
  task.event = desc.sample_argv().event();
  task.container_name = desc.container_name();
  return task;
}

}  // namespace

int main() {
  std::signal(SIGINT, on_signal);
  std::signal(SIGTERM, on_signal);

  Config config = load_config();
  auto channel = grpc::CreateChannel(
      config.grpc_addr, grpc::InsecureChannelCredentials());
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
    return notify_result(*result_stub, config, result);
  };
  result_outbox.replay(deliver_pending);

  std::mutex result_mutex;
  std::optional<TaskResult> completed;
  std::thread worker;
  std::atomic<bool> cancel_requested{false};
  std::atomic<bool> worker_running{false};
  std::string active_task_id;

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
                  << "\",\"ok\":" << (completed->ok ? "true" : "false")
                  << "}\n";
        completed.reset();
        active_task_id.clear();
        worker_running.store(false);
        cancel_requested.store(false);
      }
    }

    result_outbox.replay(deliver_pending);

    const auto response = heartbeat(
        *health_stub, config, worker_running.load(), active_task_id);
    if (response.has_value()) {
      if (!response->cancel_task_id().empty() &&
          response->cancel_task_id() == active_task_id) {
        cancel_requested.store(true);
      }
      if (!worker_running.load() && response->pending() &&
          !response->task_desc().task_id().empty()) {
        const Task task = task_from_proto(response->task_desc());
        active_task_id = task.id;
        worker_running.store(true);
        worker = std::thread([&, task]() {
          TaskResult result = execute_task(config, task, cancel_requested);
          std::lock_guard<std::mutex> lock(result_mutex);
          completed = std::move(result);
        });
        std::cout << "{\"level\":\"info\",\"event\":\"task_started\","
                  << "\"task_id\":\"" << json_escape(task.id) << "\","
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
