#include "collector.h"

#include "artifact_uploader.h"
#include "process_runner.h"

#include <filesystem>
#include <fstream>
#include <sstream>
#include <vector>

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

class PerfCollector final : public Collector {
 public:
  std::string name() const override { return "perf_cpu"; }
  int profiler_type() const override { return 0; }

  TaskResult collect(
      const Config& config,
      const Task& task,
      const std::atomic<bool>& stop_requested,
      std::atomic<bool>& cancel_requested) const override {
    TaskResult result;
    result.task_id = task.id;
    if (task.pid <= 0 || !fs::exists("/proc/" + std::to_string(task.pid))) {
      result.error = "target PID does not exist: " + std::to_string(task.pid);
      return result;
    }
    if (task.hz < 1 || task.hz > 10000 || task.duration < 1 || task.duration > 600) {
      result.error = "task sampling parameters exceed the runner policy";
      return result;
    }

    const fs::path output_dir = fs::path("/tmp/mini-drop-native") / task.id;
    fs::create_directories(output_dir);
    const fs::path perf_data = output_dir / "perf.data";
    const fs::path stderr_path = output_dir / "perf.stderr";

    int namespace_pid = task.pid;
    bool namespace_detected = false;
    if (!task.container_name.empty()) {
      std::ifstream status_file("/proc/" + std::to_string(task.pid) + "/status");
      std::string line;
      while (std::getline(status_file, line)) {
        if (line.rfind("NSpid:", 0) != 0) continue;
        std::istringstream values(line.substr(6));
        int value = 0;
        while (values >> value) namespace_pid = value;
        namespace_detected = namespace_pid > 0 && namespace_pid != task.pid;
        break;
      }
    }

    const std::vector<std::string> command = {
        "perf", "record", "--all-user", "-F", std::to_string(task.hz),
        "-g", "--call-graph", task.callgraph.empty() ? "fp" : task.callgraph,
        "-e", task.event.empty() ? "cpu-cycles:u" : task.event,
        "-p", std::to_string(task.pid), "-o", perf_data.string(),
        "--", "sleep", std::to_string(task.duration)};

    ProcessGroupRunner runner(config, stop_requested);
    const CommandResult command_result = runner.run(
        command, std::max(task.timeout, task.duration + 15), stderr_path,
        cancel_requested);
    if (command_result.cancelled) {
      result.error = "task cancelled; collector process group terminated";
      return result;
    }
    if (command_result.timed_out) {
      result.error = "perf task timed out; collector process group terminated";
      return result;
    }
    if (command_result.exit_code != 0 || !fs::exists(perf_data)) {
      result.error = "perf record failed (exit=" +
          std::to_string(command_result.exit_code) + "): " + first_line(stderr_path);
      return result;
    }

    const std::string object_key = "tasks/" + task.id + "/perf.data";
    const std::string digest = sha256_file(perf_data);
    if (digest.empty()) {
      result.error = "failed to compute artifact SHA-256";
      return result;
    }
    if (!upload_artifact(config, perf_data, object_key, result.error)) return result;

    std::ostringstream artifact;
    artifact << "[{\"artifact_type\":\"raw\",\"filename\":\"perf.data\",";
    artifact << "\"bucket\":\"" << escape_json(config.minio_bucket) << "\",";
    artifact << "\"object_key\":\"" << escape_json(object_key) << "\",";
    artifact << "\"content_type\":\"application/octet-stream\",";
    const auto size = fs::file_size(perf_data);
    artifact << "\"size_bytes\":" << size << ",";
    artifact << "\"sha256\":\"" << digest << "\",";
    artifact << "\"manifest\":{\"schema_version\":\"mini-drop.artifact.v1\",";
    artifact << "\"task_id\":\"" << escape_json(task.id)
             << "\",\"artifact_type\":\"raw\",\"object_key\":\""
             << escape_json(object_key) << "\",\"content_type\":\"application/octet-stream\",";
    artifact << "\"size_bytes\":" << size << ",\"sha256\":\""
             << digest << "\"},";
    artifact << "\"metadata\":{\"agent_runtime\":\"native-cpp\",";
    artifact << "\"collector_plugin\":\"perf_cpu\",\"runner\":\"process-group\",";
    artifact << "\"namespace_mode\":\""
             << (namespace_detected ? "host-pid-mapped" : "host") << "\",";
    artifact << "\"host_pid\":" << task.pid << ",\"namespace_pid\":"
             << namespace_pid << ",\"contract_version\":\"1.0.0\"}}]";
    result.ok = true;
    result.artifact_json = artifact.str();
    return result;
  }
};

}  // namespace

std::unique_ptr<Collector> make_perf_collector() {
  return std::make_unique<PerfCollector>();
}

}  // namespace mini_drop_native
