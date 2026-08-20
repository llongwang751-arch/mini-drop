#include "collector.h"

#include "artifact_uploader.h"
#include "process_runner.h"

#include <filesystem>
#include <fstream>
#include <map>
#include <regex>
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

std::string read_text(const fs::path& path) {
  std::ifstream input(path);
  return std::string(
      std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>());
}

long long bucket_value(const std::string& raw) {
  if (raw.empty()) return 0;
  const char suffix = raw.back();
  long long multiplier = 1;
  std::string number = raw;
  if (suffix == 'K' || suffix == 'k') { multiplier = 1000; number.pop_back(); }
  if (suffix == 'M' || suffix == 'm') { multiplier = 1000000; number.pop_back(); }
  return std::stoll(number) * multiplier;
}

class EbpfIoCollector final : public Collector {
 public:
  std::string name() const override { return "ebpf_io"; }
  int profiler_type() const override { return 4; }

  TaskResult collect(
      const Config& config,
      const Task& task,
      const std::atomic<bool>& stop_requested,
      std::atomic<bool>& cancel_requested) const override {
    TaskResult result;
    result.task_id = task.id;
    if (task.duration < 1 || task.duration > 600) {
      result.error = "eBPF duration must be between 1 and 600 seconds";
      return result;
    }
    if (!fs::exists("/usr/bin/bpftrace") && !fs::exists("/usr/local/bin/bpftrace")) {
      result.error = "bpftrace is not installed in native Agent image";
      return result;
    }
    if (!fs::exists("/sys/kernel/tracing/available_events") &&
        !fs::exists("/sys/kernel/debug/tracing/available_events")) {
      result.error =
          "tracefs is unavailable; run on native Linux with BPF/PERFMON permissions";
      return result;
    }

    const fs::path output_dir = fs::path("/tmp/mini-drop-native") / task.id;
    fs::create_directories(output_dir);
    const fs::path script_path = output_dir / "io_latency.bt";
    const fs::path raw_path = output_dir / "io_latency.txt";
    const fs::path stderr_path = output_dir / "bpftrace.stderr";
    const fs::path metrics_path = output_dir / "ebpf_metrics.json";

    std::string script = read_text("/opt/mini-drop/io_latency.bt");
    if (script.empty()) {
      result.error = "eBPF script is missing from native Agent image";
      return result;
    }
    script += "\ninterval:s:" + std::to_string(task.duration) + " { exit(); }\n";
    { std::ofstream output(script_path); output << script; }

    ProcessGroupRunner runner(config, stop_requested);
    const std::vector<std::string> command = {
        "bpftrace", "--include", "/opt/mini-drop/bpftrace_compat.h",
        "-o", raw_path.string(), script_path.string()};
    const CommandResult command_result = runner.run(
        command, std::max(task.timeout, task.duration + 15), stderr_path,
        cancel_requested);
    if (command_result.cancelled) {
      result.error = "eBPF task cancelled; probe process group terminated";
      return result;
    }
    if (command_result.timed_out) {
      result.error = "eBPF task timed out; probe process group terminated";
      return result;
    }
    if (command_result.exit_code != 0 || !fs::exists(raw_path)) {
      result.error = "bpftrace failed (exit=" +
          std::to_string(command_result.exit_code) + "): " +
          read_text(stderr_path).substr(0, 300);
      return result;
    }

    std::map<std::string, long long> histogram;
    const std::regex row(
        R"(\[\s*(\d+[KkMm]?)\s*,\s*(\d+[KkMm]?)\s*\)\s+(\d+))");
    const std::string raw = read_text(raw_path);
    for (auto it = std::sregex_iterator(raw.begin(), raw.end(), row);
         it != std::sregex_iterator(); ++it) {
      const auto& match = *it;
      const std::string key = "[" + std::to_string(bucket_value(match[1])) +
          ", " + std::to_string(bucket_value(match[2])) + ")";
      histogram[key] = std::stoll(match[3]);
    }
    long long total = 0;
    std::ostringstream metrics;
    metrics << "{\"schema_version\":\"ebpf_io.v1\",\"io_latency_us\":{";
    bool first = true;
    for (const auto& [bucket, count] : histogram) {
      if (!first) metrics << ',';
      first = false;
      metrics << '"' << escape_json(bucket) << "\":" << count;
      total += count;
    }
    metrics << "},\"total_samples\":" << total
            << ",\"collector_runtime\":\"native-cpp\"}";
    { std::ofstream output(metrics_path); output << metrics.str(); }

    if (total == 0) {
      result.error =
          "eBPF probe completed but collected zero block IO samples; "
          "generate real disk IO during the sampling window";
      return result;
    }

    const std::string base_key = "tasks/" + task.id + "/";
    const std::string metrics_key = base_key + "ebpf_metrics.json";
    const std::string raw_key = base_key + "io_latency.txt";
    const std::string metrics_digest = sha256_file(metrics_path);
    const std::string raw_digest = sha256_file(raw_path);
    if (metrics_digest.empty() || raw_digest.empty()) {
      result.error = "failed to compute eBPF artifact SHA-256";
      return result;
    }
    if (!upload_artifact(config, metrics_path, metrics_key, result.error) ||
        !upload_artifact(config, raw_path, raw_key, result.error)) return result;

    const auto metrics_size = fs::file_size(metrics_path);
    const auto raw_size = fs::file_size(raw_path);

    std::ostringstream artifacts;
    artifacts << "[{\"artifact_type\":\"ebpf_metrics\",";
    artifacts << "\"filename\":\"ebpf_metrics.json\",\"bucket\":\""
              << escape_json(config.minio_bucket) << "\",\"object_key\":\""
              << escape_json(metrics_key) << "\",\"content_type\":\"application/json\",";
    artifacts << "\"size_bytes\":" << metrics_size
              << ",\"sha256\":\"" << metrics_digest << "\",";
    artifacts << "\"manifest\":{\"schema_version\":\"mini-drop.artifact.v1\",";
    artifacts << "\"task_id\":\"" << escape_json(task.id)
              << "\",\"artifact_type\":\"ebpf_metrics\",\"object_key\":\""
              << escape_json(metrics_key) << "\",\"content_type\":\"application/json\",";
    artifacts << "\"size_bytes\":" << metrics_size << ",\"sha256\":\""
              << metrics_digest << "\"},";
    artifacts << "\"metadata\":{\"schema_version\":\"ebpf_io.v1\",";
    artifacts << "\"total_samples\":" << total << ",\"agent_runtime\":\"native-cpp\",";
    artifacts << "\"collector_plugin\":\"ebpf_io\",\"probe\":\"block tracepoint\"}},";
    artifacts << "{\"artifact_type\":\"ebpf_raw\",\"filename\":\"io_latency.txt\",";
    artifacts << "\"bucket\":\"" << escape_json(config.minio_bucket)
              << "\",\"object_key\":\"" << escape_json(raw_key)
              << "\",\"content_type\":\"text/plain\",\"size_bytes\":"
              << raw_size << ",\"sha256\":\"" << raw_digest << "\",";
    artifacts << "\"manifest\":{\"schema_version\":\"mini-drop.artifact.v1\",";
    artifacts << "\"task_id\":\"" << escape_json(task.id)
              << "\",\"artifact_type\":\"ebpf_raw\",\"object_key\":\""
              << escape_json(raw_key) << "\",\"content_type\":\"text/plain\",";
    artifacts << "\"size_bytes\":" << raw_size << ",\"sha256\":\""
              << raw_digest << "\"},\"metadata\":{\"agent_runtime\":\"native-cpp\"}}]";
    result.ok = true;
    result.artifact_json = artifacts.str();
    return result;
  }
};

}  // namespace

std::unique_ptr<Collector> make_ebpf_io_collector() {
  return std::make_unique<EbpfIoCollector>();
}

}  // namespace mini_drop_native
