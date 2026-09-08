#include "collector.h"

#include "artifact_uploader.h"
#include "process_runner.h"

#include <filesystem>
#include <fstream>
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <sstream>
#include <vector>

#include <unistd.h>

namespace fs = std::filesystem;

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

std::string first_line(const fs::path& path) {
  std::ifstream input(path);
  std::string line;
  std::getline(input, line);
  return line;
}

std::string bounded_json_object(const fs::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) return "";
  std::ostringstream buffer;
  char chunk[4096];
  std::size_t total = 0;
  while (input && total <= 1024 * 1024) {
    input.read(chunk, sizeof(chunk));
    const auto count = static_cast<std::size_t>(input.gcount());
    total += count;
    if (total > 1024 * 1024) return "";
    buffer.write(chunk, static_cast<std::streamsize>(count));
  }
  std::string value = buffer.str();
  const auto first = value.find_first_not_of(" \t\r\n");
  const auto last = value.find_last_not_of(" \t\r\n");
  if (first == std::string::npos || value[first] != '{' || value[last] != '}') {
    return "";
  }
  return value.substr(first, last - first + 1);
}

long long unix_time_millis() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::system_clock::now().time_since_epoch()).count();
}

bool executable_exists(const std::string& path) {
  return fs::exists(path) && ::access(path.c_str(), X_OK) == 0;
}

TaskResult run_file_collector(
    const Config& config,
    const Task& task,
    const std::atomic<bool>& stop_requested,
    std::atomic<bool>& cancel_requested,
    const std::string& collector,
    const std::string& filename,
    const std::string& content_type,
    const std::vector<std::string>& command,
    const fs::path& source_after_command = {},
    const std::string& artifact_type = "raw") {
  TaskResult result;
  result.task_id = task.id;
  if (task.pid <= 0 || !fs::exists("/proc/" + std::to_string(task.pid))) {
    result.error = "target PID does not exist: " + std::to_string(task.pid);
    return result;
  }
  if (task.duration < 1 || task.duration > 600 || task.hz < 1 || task.hz > 10000) {
    result.error = "task sampling parameters exceed native runner policy";
    return result;
  }
  const fs::path output_dir =
      fs::path("/tmp/mini-drop-native") / task.id / task.task_attempt_id;
  fs::create_directories(output_dir);
  const fs::path artifact_path = output_dir / filename;
  const fs::path stderr_path = output_dir / (collector + ".stderr");
  if (!source_after_command.empty()) {
    std::error_code remove_error;
    fs::remove(source_after_command, remove_error);
  }
  ProcessGroupRunner runner(config, stop_requested);
  const CommandResult command_result = runner.run(
      command, std::max(task.timeout, task.duration + 20), stderr_path,
      cancel_requested);
  if (command_result.cancelled) {
    result.error = collector + " task cancelled; process group terminated";
    return result;
  }
  if (command_result.timed_out) {
    result.error = collector + " task timed out; process group terminated";
    return result;
  }
  if (!source_after_command.empty() && fs::exists(source_after_command)) {
    std::error_code copy_error;
    fs::copy_file(source_after_command, artifact_path,
                  fs::copy_options::overwrite_existing, copy_error);
    if (copy_error) {
      result.error = collector + " could not copy target-namespace output: " +
          copy_error.message();
      return result;
    }
    std::error_code remove_error;
    fs::remove(source_after_command, remove_error);
  }
  if (command_result.exit_code != 0 || !fs::exists(artifact_path) ||
      fs::file_size(artifact_path) == 0) {
    result.error = collector + " failed (exit=" +
        std::to_string(command_result.exit_code) + "): " + first_line(stderr_path);
    return result;
  }
  const std::string object_key = authorized_object_key(task, filename);
  if (object_key.empty()) {
    result.error = "missing exact upload target for " + filename;
    return result;
  }
  const std::string digest = sha256_file(artifact_path);
  if (digest.empty()) {
    result.error = "failed to compute artifact SHA-256";
    return result;
  }
  if (!upload_artifact(task, artifact_path, object_key, result.error)) return result;
  const auto size = fs::file_size(artifact_path);
  std::ostringstream artifact;
  artifact << "[{\"artifact_type\":\"" << escape_json(artifact_type)
           << "\",\"filename\":\""
           << escape_json(filename) << "\",\"bucket\":\""
           << escape_json(config.minio_bucket) << "\",\"object_key\":\""
           << escape_json(object_key) << "\",\"content_type\":\""
           << escape_json(content_type) << "\",\"size_bytes\":" << size
           << ",\"sha256\":\"" << digest << "\""
           << ",\"manifest\":{\"schema_version\":\"mini-drop.artifact.v1\""
           << ",\"task_id\":\"" << escape_json(task.id) << "\""
           << ",\"task_attempt_id\":\"" << escape_json(task.task_attempt_id) << "\""
           << ",\"artifact_type\":\"" << escape_json(artifact_type)
           << "\",\"object_key\":\""
           << escape_json(object_key) << "\",\"content_type\":\""
           << escape_json(content_type) << "\",\"size_bytes\":" << size
           << ",\"sha256\":\"" << digest << "\"}"
           << ",\"metadata\":{\"collector_runtime\":\"native-cpp\","
           << "\"collector_plugin\":\"" << escape_json(collector)
           << "\",\"contract_version\":\"1.0.0\"}}]";
  result.ok = true;
  result.artifact_json = artifact.str();
  return result;
}

class PySpyCollector final : public Collector {
 public:
  std::string name() const override { return "pyspy"; }
  int profiler_type() const override { return 3; }
  TaskResult collect(const Config& config, const Task& task,
      const std::atomic<bool>& stop, std::atomic<bool>& cancel) const override {
    if (!executable_exists("/usr/local/bin/py-spy") &&
        !executable_exists("/usr/bin/py-spy")) {
      return {task.id, false, "py-spy is not installed in native Agent image", ""};
    }
    const fs::path output = fs::path("/tmp/mini-drop-native") / task.id /
        task.task_attempt_id / "pyspy-speedscope.json";
    return run_file_collector(config, task, stop, cancel, name(),
        "pyspy-speedscope.json", "application/json",
        {"py-spy", "record", "--pid", std::to_string(task.pid), "--rate",
         std::to_string(task.hz), "--duration", std::to_string(task.duration),
         "--format", "speedscope", "--output", output.string(), "--nonblocking"});
  }
};

class AsyncProfilerCollector final : public Collector {
 public:
  std::string name() const override { return "java_async"; }
  int profiler_type() const override { return 1; }
  bool available() const override {
    const char* configured = std::getenv("ASYNC_PROFILER_BIN");
    const std::string binary = configured ? configured : "/opt/async-profiler/bin/asprof";
    return executable_exists(binary);
  }
  TaskResult collect(const Config& config, const Task& task,
      const std::atomic<bool>& stop, std::atomic<bool>& cancel) const override {
    const char* configured = std::getenv("ASYNC_PROFILER_BIN");
    const std::string binary = configured ? configured : "/opt/async-profiler/bin/asprof";
    if (!executable_exists(binary)) {
      return {task.id, false,
              "async-profiler is not installed; mount it at /opt/async-profiler or set ASYNC_PROFILER_BIN",
              ""};
    }
    // async-profiler asks the target JVM to open the output path.  A nested
    // directory created only in the Agent mount namespace is therefore not
    // visible to a JVM running in another container.  Write to the target's
    // /tmp and retrieve the file through /proc/<pid>/root afterwards.
    const std::string target_filename = "mini-drop-" + task.id + ".html";
    const fs::path target_output = fs::path("/tmp") / target_filename;
    const fs::path source_after_command =
        fs::path("/proc") / std::to_string(task.pid) / "root/tmp" / target_filename;
    const std::string event =
        task.event == "alloc" || task.event == "lock" || task.event == "wall"
            ? task.event
            : "cpu";
    const fs::path metrics_source =
        fs::path("/proc") / std::to_string(task.pid) /
        "root/tmp/mini-drop-jvm-metrics.json";
    const std::string metrics_before = bounded_json_object(metrics_source);
    const long long metrics_before_ms = unix_time_millis();
    TaskResult result = run_file_collector(config, task, stop, cancel, name(),
        "java-flamegraph.html", "text/html; charset=utf-8",
        {binary, "-d", std::to_string(task.duration), "-e", event, "-o",
         "flamegraph", "-f", target_output.string(), std::to_string(task.pid)},
        source_after_command, "java_flamegraph_html");
    if (!result.ok || metrics_before.empty()) return result;

    const std::string metrics_after = bounded_json_object(metrics_source);
    const long long metrics_after_ms = unix_time_millis();
    if (metrics_after.empty()) return result;
    const fs::path output_dir =
        fs::path("/tmp/mini-drop-native") / task.id / task.task_attempt_id;
    const fs::path metrics_path = output_dir / "jvm-gc-metrics.json";
    std::ofstream metrics(metrics_path);
    metrics << "{\"schema_version\":\"jvm_gc_metrics.v1\""
            << ",\"event\":\"" << escape_json(event) << "\""
            << ",\"before_captured_at_unix_ms\":" << metrics_before_ms
            << ",\"after_captured_at_unix_ms\":" << metrics_after_ms
            << ",\"before\":" << metrics_before
            << ",\"after\":" << metrics_after << '}';
    metrics.close();
    if (!metrics || fs::file_size(metrics_path) == 0) return result;

    const std::string object_key = authorized_object_key(task, "jvm-gc-metrics.json");
    if (object_key.empty()) return result;
    const std::string digest = sha256_file(metrics_path);
    if (digest.empty()) return result;
    std::string upload_error;
    if (!upload_artifact(task, metrics_path, object_key, upload_error)) return result;

    const auto size = fs::file_size(metrics_path);
    std::ostringstream artifact;
    artifact << "{\"artifact_type\":\"jvm_gc_metrics\""
             << ",\"filename\":\"jvm-gc-metrics.json\""
             << ",\"bucket\":\"" << escape_json(config.minio_bucket) << "\""
             << ",\"object_key\":\"" << escape_json(object_key) << "\""
             << ",\"content_type\":\"application/json\""
             << ",\"size_bytes\":" << size
             << ",\"sha256\":\"" << digest << "\""
             << ",\"manifest\":{\"schema_version\":\"mini-drop.artifact.v1\""
             << ",\"task_id\":\"" << escape_json(task.id) << "\""
             << ",\"task_attempt_id\":\"" << escape_json(task.task_attempt_id) << "\""
             << ",\"artifact_type\":\"jvm_gc_metrics\""
             << ",\"object_key\":\"" << escape_json(object_key) << "\""
             << ",\"content_type\":\"application/json\""
             << ",\"size_bytes\":" << size
             << ",\"sha256\":\"" << digest << "\"}"
             << ",\"metadata\":{\"collector_runtime\":\"native-cpp\""
             << ",\"collector_plugin\":\"java_async\""
             << ",\"contract_version\":\"1.0.0\"}}";
    if (!result.artifact_json.empty() && result.artifact_json.back() == ']') {
      result.artifact_json.pop_back();
      result.artifact_json += "," + artifact.str() + "]";
    }
    return result;
  }
};

class GoPprofCollector final : public Collector {
 public:
  std::string name() const override { return "go_pprof"; }
  int profiler_type() const override { return 2; }
  TaskResult collect(const Config& config, const Task& task,
      const std::atomic<bool>& stop, std::atomic<bool>& cancel) const override {
    std::string base = task.event;
    if (base.rfind("http://", 0) != 0 && base.rfind("https://", 0) != 0) {
      base = "http://go-hotspot:6060/debug/pprof/profile";
    }
    const std::string separator = base.find('?') == std::string::npos ? "?" : "&";
    const std::string url = base + separator + "seconds=" + std::to_string(task.duration);
    const fs::path output = fs::path("/tmp/mini-drop-native") / task.id /
        task.task_attempt_id / "go-cpu.pprof";
    return run_file_collector(config, task, stop, cancel, name(),
        "go-cpu.pprof", "application/octet-stream",
        {"curl", "--fail", "--silent", "--show-error", "--max-time",
         std::to_string(task.duration + 10), "--output", output.string(), url});
  }
};

}  // namespace

std::unique_ptr<Collector> make_async_profiler_collector() {
  return std::make_unique<AsyncProfilerCollector>();
}
std::unique_ptr<Collector> make_go_pprof_collector() {
  return std::make_unique<GoPprofCollector>();
}
std::unique_ptr<Collector> make_pyspy_collector() {
  return std::make_unique<PySpyCollector>();
}

}  // namespace mini_drop_native
