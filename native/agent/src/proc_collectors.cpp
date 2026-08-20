#include "collector.h"

#include "artifact_uploader.h"
#include "process_runner.h"

#include <chrono>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <thread>
#include <vector>

namespace fs = std::filesystem;
using namespace std::chrono_literals;

namespace mini_drop_native {
namespace {

std::string escape_json(const std::string& value) {
  std::ostringstream out;
  for (const unsigned char ch : value) {
    if (ch == '\\' || ch == '"') out << '\\' << ch;
    else if (ch == '\n') out << "\\n";
    else if (ch == '\r') out << "\\r";
    else if (ch == '\t') out << "\\t";
    else out << ch;
  }
  return out.str();
}

std::string read_text(const fs::path& path) {
  std::ifstream input(path);
  return std::string(std::istreambuf_iterator<char>(input),
                     std::istreambuf_iterator<char>());
}

long long value_kb(const std::string& text, const std::string& key) {
  std::istringstream input(text);
  std::string line;
  while (std::getline(input, line)) {
    if (line.rfind(key + ":", 0) != 0) continue;
    std::istringstream value(line.substr(key.size() + 1));
    long long result = 0;
    value >> result;
    return result;
  }
  return 0;
}

TaskResult upload_json(const Config& config, const Task& task,
                       const std::string& collector, const fs::path& path,
                       const std::string& artifact_type) {
  TaskResult result;
  result.task_id = task.id;
  const std::string object_key = "tasks/" + task.id + "/" + path.filename().string();
  const std::string digest = sha256_file(path);
  if (digest.empty()) {
    result.error = "failed to compute artifact SHA-256";
    return result;
  }
  if (!upload_artifact(config, path, object_key, result.error)) return result;
  const auto size = fs::file_size(path);
  std::ostringstream artifacts;
  artifacts << "[{\"artifact_type\":\"" << artifact_type
            << "\",\"filename\":\"" << escape_json(path.filename().string())
            << "\",\"bucket\":\"" << escape_json(config.minio_bucket)
            << "\",\"object_key\":\"" << escape_json(object_key)
            << "\",\"content_type\":\"application/json\",\"size_bytes\":" << size
            << ",\"sha256\":\"" << digest << "\""
            << ",\"manifest\":{\"schema_version\":\"mini-drop.artifact.v1\""
            << ",\"task_id\":\"" << escape_json(task.id) << "\""
            << ",\"artifact_type\":\"" << escape_json(artifact_type) << "\""
            << ",\"object_key\":\"" << escape_json(object_key) << "\""
            << ",\"content_type\":\"application/json\",\"size_bytes\":" << size
            << ",\"sha256\":\"" << digest << "\"}"
            << ",\"metadata\":{\"agent_runtime\":\"native-cpp\","
            << "\"collector_plugin\":\"" << collector
            << "\",\"contract_version\":\"1.0.0\"}}]";
  result.ok = true;
  result.artifact_json = artifacts.str();
  return result;
}

bool validate(const Task& task, TaskResult& result) {
  result.task_id = task.id;
  if (task.pid <= 0 || !fs::exists("/proc/" + std::to_string(task.pid))) {
    result.error = "target PID does not exist: " + std::to_string(task.pid);
    return false;
  }
  if (task.duration < 1 || task.duration > 600) {
    result.error = "duration must be between 1 and 600 seconds";
    return false;
  }
  return true;
}

class MemoryCollector final : public Collector {
 public:
  std::string name() const override { return "memory_smaps"; }
  int profiler_type() const override { return 5; }
  TaskResult collect(const Config& config, const Task& task,
      const std::atomic<bool>& stop, std::atomic<bool>& cancel) const override {
    TaskResult result;
    if (!validate(task, result)) return result;
    const fs::path dir = fs::path("/tmp/mini-drop-native") / task.id;
    fs::create_directories(dir);
    const fs::path output_path = dir / "memory.json";
    std::ofstream output(output_path);
    output << "{\"schema_version\":\"memory.v1\",\"pid\":" << task.pid
           << ",\"samples\":[";
    bool first = true;
    for (int second = 0; second < task.duration; ++second) {
      if (stop.load() || cancel.load()) {
        result.error = "memory collection cancelled";
        return result;
      }
      std::string raw = read_text("/proc/" + std::to_string(task.pid) + "/smaps_rollup");
      if (raw.empty()) raw = read_text("/proc/" + std::to_string(task.pid) + "/status");
      if (!first) output << ',';
      first = false;
      output << "{\"offset_sec\":" << second
             << ",\"rss_kb\":" << value_kb(raw, "Rss")
             << ",\"pss_kb\":" << value_kb(raw, "Pss")
             << ",\"swap_kb\":" << value_kb(raw, "Swap") << '}';
      if (second + 1 < task.duration) std::this_thread::sleep_for(1s);
    }
    output << "]}";
    output.close();
    return upload_json(config, task, name(), output_path, "memory_json");
  }
};

class SysMetricsCollector final : public Collector {
 public:
  std::string name() const override { return "sys_metrics"; }
  int profiler_type() const override { return 6; }
  TaskResult collect(const Config& config, const Task& task,
      const std::atomic<bool>& stop, std::atomic<bool>& cancel) const override {
    TaskResult result;
    if (!validate(task, result)) return result;
    const fs::path dir = fs::path("/tmp/mini-drop-native") / task.id;
    fs::create_directories(dir);
    const fs::path output_path = dir / "sys_metrics.json";
    const fs::path proc = fs::path("/proc") / std::to_string(task.pid);
    std::ofstream output(output_path);
    output << "{\"schema_version\":\"sys_metrics.v1\",\"pid\":" << task.pid
           << ",\"samples\":[";
    bool first = true;
    for (int second = 0; second < task.duration; ++second) {
      if (stop.load() || cancel.load()) {
        result.error = "system metrics collection cancelled";
        return result;
      }
      const std::string status = read_text(proc / "status");
      std::error_code ec;
      long long fd_count = 0;
      for (fs::directory_iterator it(proc / "fd", ec), end; !ec && it != end; it.increment(ec)) {
        ++fd_count;
      }
      if (!first) output << ',';
      first = false;
      output << "{\"offset_sec\":" << second
             << ",\"rss_kb\":" << value_kb(status, "VmRSS")
             << ",\"threads\":" << value_kb(status, "Threads")
             << ",\"fd_count\":" << fd_count << '}';
      if (second + 1 < task.duration) std::this_thread::sleep_for(1s);
    }
    output << "]}";
    output.close();
    return upload_json(config, task, name(), output_path, "sys_metrics");
  }
};

class ContinuousPerfCollector final : public Collector {
 public:
  std::string name() const override { return "continuous_perf"; }
  int profiler_type() const override { return 7; }
  TaskResult collect(const Config& config, const Task& task,
      const std::atomic<bool>& stop, std::atomic<bool>& cancel) const override {
    TaskResult result;
    if (!validate(task, result)) return result;
    const fs::path dir = fs::path("/tmp/mini-drop-native") / task.id;
    fs::create_directories(dir);
    const fs::path perf_data = dir / "continuous-perf.data";
    const fs::path stderr_path = dir / "continuous-perf.stderr";
    ProcessGroupRunner runner(config, stop);
    const CommandResult command_result = runner.run(
        {"perf", "record", "--all-user", "-F", std::to_string(task.hz), "-g",
         "--call-graph", task.callgraph.empty() ? "fp" : task.callgraph,
         "-p", std::to_string(task.pid), "-o", perf_data.string(), "--", "sleep",
         std::to_string(task.duration)},
        std::max(task.timeout, task.duration + 15), stderr_path, cancel);
    if (command_result.cancelled || command_result.timed_out ||
        command_result.exit_code != 0 || !fs::exists(perf_data)) {
      result.error = command_result.cancelled ? "continuous perf cancelled" :
          command_result.timed_out ? "continuous perf timed out" :
          "continuous perf failed: " + read_text(stderr_path).substr(0, 300);
      return result;
    }
    const std::string object_key = "tasks/" + task.id + "/continuous-perf.data";
    const std::string digest = sha256_file(perf_data);
    if (digest.empty()) {
      result.error = "failed to compute artifact SHA-256";
      return result;
    }
    if (!upload_artifact(config, perf_data, object_key, result.error)) return result;
    const auto size = fs::file_size(perf_data);
    std::ostringstream artifacts;
    artifacts << "[{\"artifact_type\":\"continuous_raw\","
              << "\"filename\":\"continuous-perf.data\",\"bucket\":\""
              << escape_json(config.minio_bucket) << "\",\"object_key\":\""
              << escape_json(object_key) << "\",\"content_type\":\"application/octet-stream\","
              << "\"size_bytes\":" << size
              << ",\"sha256\":\"" << digest << "\""
              << ",\"manifest\":{\"schema_version\":\"mini-drop.artifact.v1\""
              << ",\"task_id\":\"" << escape_json(task.id) << "\""
              << ",\"artifact_type\":\"continuous_raw\",\"object_key\":\""
              << escape_json(object_key) << "\",\"content_type\":\"application/octet-stream\""
              << ",\"size_bytes\":" << size << ",\"sha256\":\"" << digest << "\"}"
              << ",\"metadata\":{\"agent_runtime\":\"native-cpp\","
              << "\"collector_plugin\":\"continuous_perf\",\"window_seconds\":"
              << task.duration << ",\"contract_version\":\"1.0.0\"}}]";
    result.ok = true;
    result.artifact_json = artifacts.str();
    return result;
  }
};

}  // namespace

std::unique_ptr<Collector> make_memory_collector() {
  return std::make_unique<MemoryCollector>();
}
std::unique_ptr<Collector> make_sys_metrics_collector() {
  return std::make_unique<SysMetricsCollector>();
}
std::unique_ptr<Collector> make_continuous_perf_collector() {
  return std::make_unique<ContinuousPerfCollector>();
}

}  // namespace mini_drop_native
