#include "collector.h"

#include "artifact_uploader.h"
#include "process_runner.h"

#include <filesystem>
#include <fstream>
#include <sstream>
#include <vector>
#include <algorithm>

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

    const fs::path output_dir =
        fs::path("/tmp/mini-drop-native") / task.id / task.task_attempt_id;
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

    const std::string requested_event =
        task.event.empty() ? "cpu-cycles:u" : task.event;
    auto perf_command = [&](const std::string& event, const fs::path& output,
                            int duration) {
      return std::vector<std::string>{
        "perf", "record", "--all-user", "-F", std::to_string(task.hz),
        "-g", "--call-graph", task.callgraph.empty() ? "fp" : task.callgraph,
        "-e", event, "-p", std::to_string(task.pid), "-o", output.string(),
        "--", "sleep", std::to_string(duration)};
    };

    ProcessGroupRunner runner(config, stop_requested);
    CommandResult command_result = runner.run(
        perf_command(requested_event, perf_data, task.duration),
        std::max(task.timeout, task.duration + 15), stderr_path,
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

    // Hardware cycles can legally produce a header-only perf.data on some
    // virtual machines even though `perf record` exits with status 0.  Such a
    // file used to reach the analyzer and fail as ANALYSIS_INPUT_INVALID.  For
    // the default cycles event only, make one bounded, real re-collection with
    // the portable software cpu-clock event.  We keep this as an explicit
    // recovery path and record it in artifact metadata; custom events are
    // never silently replaced.
    std::string recorded_event = requested_event;
    bool software_event_recovery = false;
    constexpr std::uintmax_t kHeaderOnlyThresholdBytes = 16 * 1024;
    if (fs::file_size(perf_data) < kHeaderOnlyThresholdBytes &&
        (requested_event == "cpu-cycles" || requested_event == "cpu-cycles:u")) {
      const int retry_duration = std::min(task.duration, 15);
      const fs::path retry_data = output_dir / "perf-retry.data";
      const fs::path retry_stderr = output_dir / "perf-retry.stderr";
      command_result = runner.run(
          perf_command("cpu-clock:u", retry_data, retry_duration),
          std::max(task.timeout, retry_duration + 15), retry_stderr,
          cancel_requested);
      if (command_result.cancelled || command_result.timed_out ||
          command_result.exit_code != 0 || !fs::exists(retry_data) ||
          fs::file_size(retry_data) < kHeaderOnlyThresholdBytes) {
        result.error = "perf produced no samples; cpu-clock recovery failed: " +
            first_line(retry_stderr);
        return result;
      }
      fs::remove(perf_data);
      fs::rename(retry_data, perf_data);
      recorded_event = "cpu-clock:u";
      software_event_recovery = true;
    }

    const std::string object_key = authorized_object_key(task, "perf.data");
    if (object_key.empty()) {
      result.error = "missing exact upload target for perf.data";
      return result;
    }
    const std::string digest = sha256_file(perf_data);
    if (digest.empty()) {
      result.error = "failed to compute artifact SHA-256";
      return result;
    }
    if (!upload_artifact(task, perf_data, object_key, result.error)) return result;

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
             << "\",\"task_attempt_id\":\"" << escape_json(task.task_attempt_id)
             << "\",\"artifact_type\":\"raw\",\"object_key\":\""
             << escape_json(object_key) << "\",\"content_type\":\"application/octet-stream\",";
    artifact << "\"size_bytes\":" << size << ",\"sha256\":\""
             << digest << "\"},";
    artifact << "\"metadata\":{\"collector_runtime\":\"native-cpp\",";
    artifact << "\"collector_plugin\":\"perf_cpu\",\"runner\":\"process-group\",";
    artifact << "\"namespace_mode\":\""
             << (namespace_detected ? "host-pid-mapped" : "host") << "\",";
    artifact << "\"host_pid\":" << task.pid << ",\"namespace_pid\":"
             << namespace_pid << ",\"requested_event\":\""
             << escape_json(requested_event) << "\",\"recorded_event\":\""
             << escape_json(recorded_event) << "\",\"software_event_recovery\":"
             << (software_event_recovery ? "true" : "false")
             << ",\"contract_version\":\"1.0.0\"}}]";
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
