#include "collector.h"

#include "artifact_uploader.h"
#include "process_runner.h"

#include <chrono>
#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <thread>
#include <vector>
#include <ctime>
#include <unistd.h>

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

int namespace_pid_from_status(const std::string& raw, int fallback) {
  std::istringstream input(raw);
  std::string line;
  while (std::getline(input, line)) {
    if (line.rfind("NSpid:", 0) != 0) continue;
    std::istringstream values(line.substr(6));
    int value = 0;
    int innermost = 0;
    while (values >> value) {
      if (value > 0) innermost = value;
    }
    return innermost > 0 ? innermost : fallback;
  }
  return fallback;
}

bool parse_non_negative_integer(const std::string& token, long long& value) {
  if (token.empty() || !std::all_of(token.begin(), token.end(), [](unsigned char ch) {
        return std::isdigit(ch) != 0;
      })) {
    return false;
  }
  try {
    value = std::stoll(token);
    return value >= 0;
  } catch (const std::exception&) {
    return false;
  }
}

struct ProcessStatSnapshot {
  long long cpu_ticks = 0;
  long long start_ticks = 0;
  bool valid = false;
};

ProcessStatSnapshot parse_process_stat(const std::string& raw) {
  // /proc/<pid>/stat field 2 is parenthesized and may contain spaces. Parse
  // from the final ')' so field indexes remain stable for real process names.
  const auto close = raw.rfind(')');
  if (close == std::string::npos || close + 2 >= raw.size()) return {};
  std::istringstream input(raw.substr(close + 2));
  std::vector<std::string> fields;
  std::string field;
  while (input >> field) fields.push_back(field);
  // fields[0] is kernel field 3 (state). utime/stime/starttime are 14/15/22.
  if (fields.size() <= 19) return {};
  try {
    const long long user_ticks = std::stoll(fields[11]);
    const long long system_ticks = std::stoll(fields[12]);
    const long long start_ticks = std::stoll(fields[19]);
    if (user_ticks < 0 || system_ticks < 0 || start_ticks <= 0) return {};
    return {user_ticks + system_ticks, start_ticks, true};
  } catch (const std::exception&) {
    return {};
  }
}

struct HostCpuSnapshot {
  long long user = 0;
  long long nice = 0;
  long long system = 0;
  long long idle = 0;
  long long iowait = 0;
  long long irq = 0;
  long long softirq = 0;
  long long steal = 0;
  long long total = 0;
  bool valid = false;
};

HostCpuSnapshot parse_host_cpu(const std::string& raw) {
  std::istringstream lines(raw);
  std::string first_line;
  if (!std::getline(lines, first_line)) return {};
  std::istringstream input(first_line);
  std::string label;
  HostCpuSnapshot value;
  if (!(input >> label) || label != "cpu") return {};
  if (!(input >> value.user >> value.nice >> value.system >> value.idle)) {
    return {};
  }
  input >> value.iowait >> value.irq >> value.softirq >> value.steal;
  value.total = value.user + value.nice + value.system + value.idle +
                value.iowait + value.irq + value.softirq + value.steal;
  value.valid = value.total > 0;
  return value;
}

double load_one_minute(const std::string& raw) {
  std::istringstream input(raw);
  double value = 0.0;
  input >> value;
  return value < 0.0 ? 0.0 : value;
}

std::string bounded_json_object(const fs::path& path) {
  std::error_code ec;
  const auto size = fs::file_size(path, ec);
  if (ec || size == 0 || size > 128 * 1024) return "";
  std::string value = read_text(path);
  const auto begin = value.find_first_not_of(" \t\r\n");
  const auto end = value.find_last_not_of(" \t\r\n");
  if (begin == std::string::npos || end == std::string::npos ||
      value[begin] != '{' || value[end] != '}') {
    return "";
  }
  return value.substr(begin, end - begin + 1);
}

TaskResult upload_json(const Config& config, const Task& task,
                       const std::string& collector, const fs::path& path,
                       const std::string& artifact_type) {
  TaskResult result;
  result.task_id = task.id;
  const std::string object_key =
      authorized_object_key(task, path.filename().string());
  if (object_key.empty()) {
    result.error = "missing exact upload target for " + path.filename().string();
    return result;
  }
  const std::string digest = sha256_file(path);
  if (digest.empty()) {
    result.error = "failed to compute artifact SHA-256";
    return result;
  }
  if (!upload_artifact(task, path, object_key, result.error)) return result;
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
            << ",\"task_attempt_id\":\"" << escape_json(task.task_attempt_id) << "\""
            << ",\"artifact_type\":\"" << escape_json(artifact_type) << "\""
            << ",\"object_key\":\"" << escape_json(object_key) << "\""
            << ",\"content_type\":\"application/json\",\"size_bytes\":" << size
            << ",\"sha256\":\"" << digest << "\"}"
            << ",\"metadata\":{\"collector_runtime\":\"native-cpp\","
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
  if (task.duration < 1 || task.duration > 86400 ||
      (task.max_duration > 0 && task.duration > task.max_duration)) {
    result.error = "duration exceeds the task contract or resource budget";
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
    const fs::path dir =
        fs::path("/tmp/mini-drop-native") / task.id / task.task_attempt_id;
    fs::create_directories(dir);
    const fs::path output_path = dir / "memory.json";
    const fs::path proc = fs::path("/proc") / std::to_string(task.pid);
    const int namespace_pid = namespace_pid_from_status(
        read_text(proc / "status"), task.pid);
    std::ofstream output(output_path);
    output << "{\"schema_version\":\"memory.v2\",\"pid\":" << task.pid
           << ",\"namespace_pid\":" << namespace_pid
           << ",\"samples\":[";
    bool first = true;
    for (int second = 0; second < task.duration; ++second) {
      if (stop.load() || cancel.load()) {
        result.error = "memory collection cancelled";
        return result;
      }
      std::string raw = read_text("/proc/" + std::to_string(task.pid) + "/smaps_rollup");
      if (raw.empty()) raw = read_text("/proc/" + std::to_string(task.pid) + "/status");
      long long rss_kb = value_kb(raw, "Rss");
      if (rss_kb == 0) rss_kb = value_kb(raw, "VmRSS");
      const ProcessStatSnapshot process_stat =
          parse_process_stat(read_text(proc / "stat"));
      std::string application_metrics = bounded_json_object(
          proc / "root/tmp/mini-drop-app-metrics.json");
      if (application_metrics.empty()) {
        application_metrics = bounded_json_object(
            proc / "root/tmp/mini-drop-jvm-metrics.json");
      }
      if (!first) output << ',';
      first = false;
      output << "{\"offset_sec\":" << second
             << ",\"captured_at_unix_ms\":"
             << std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::system_clock::now().time_since_epoch())
                    .count()
             << ",\"rss_kb\":" << rss_kb
             << ",\"pss_kb\":" << value_kb(raw, "Pss")
             << ",\"swap_kb\":" << value_kb(raw, "Swap")
             << ",\"process_start_ticks\":" << process_stat.start_ticks;
      if (!application_metrics.empty()) {
        output << ",\"application_metrics_json\":\""
               << escape_json(application_metrics) << '\"';
      }
      output << '}';
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
    const fs::path dir =
        fs::path("/tmp/mini-drop-native") / task.id / task.task_attempt_id;
    fs::create_directories(dir);
    const fs::path output_path = dir / "sys_metrics.json";
    const fs::path proc = fs::path("/proc") / std::to_string(task.pid);
    const int namespace_pid = namespace_pid_from_status(
        read_text(proc / "status"), task.pid);
    std::ofstream output(output_path);
    output << "{\"schema_version\":\"sys_metrics.v2\",\"pid\":" << task.pid
           << ",\"namespace_pid\":" << namespace_pid
           << ",\"clock_ticks_per_second\":" << sysconf(_SC_CLK_TCK)
           << ",\"samples\":[";
    bool first = true;
    for (int second = 0; second < task.duration; ++second) {
      if (stop.load() || cancel.load()) {
        result.error = "system metrics collection cancelled";
        return result;
      }
      const std::string status = read_text(proc / "status");
      const ProcessStatSnapshot process_stat =
          parse_process_stat(read_text(proc / "stat"));
      const HostCpuSnapshot host_cpu = parse_host_cpu(read_text("/proc/stat"));
      const std::string process_io = read_text(proc / "io");
      std::string application_metrics = bounded_json_object(
          proc / "root/tmp/mini-drop-app-metrics.json");
      if (application_metrics.empty()) {
        application_metrics = bounded_json_object(
            proc / "root/tmp/mini-drop-jvm-metrics.json");
      }
      std::error_code ec;
      long long fd_count = 0;
      for (fs::directory_iterator it(proc / "fd", ec), end; !ec && it != end; it.increment(ec)) {
        ++fd_count;
      }
      if (!first) output << ',';
      first = false;
      output << "{\"offset_sec\":" << second
             << ",\"captured_at_unix_ms\":"
             << std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::system_clock::now().time_since_epoch())
                    .count()
             << ",\"rss_kb\":" << value_kb(status, "VmRSS")
             << ",\"threads\":" << value_kb(status, "Threads")
             << ",\"fd_count\":" << fd_count
             << ",\"voluntary_ctxt_switches\":"
             << value_kb(status, "voluntary_ctxt_switches")
             << ",\"nonvoluntary_ctxt_switches\":"
             << value_kb(status, "nonvoluntary_ctxt_switches")
             << ",\"process_cpu_ticks\":" << process_stat.cpu_ticks
             << ",\"process_start_ticks\":" << process_stat.start_ticks
             << ",\"process_read_bytes\":"
             << value_kb(process_io, "read_bytes")
             << ",\"process_write_bytes\":"
             << value_kb(process_io, "write_bytes")
             << ",\"load1m\":" << load_one_minute(read_text("/proc/loadavg"))
             << ",\"host_cpu_user_ticks\":" << host_cpu.user
             << ",\"host_cpu_nice_ticks\":" << host_cpu.nice
             << ",\"host_cpu_system_ticks\":" << host_cpu.system
             << ",\"host_cpu_idle_ticks\":" << host_cpu.idle
             << ",\"host_cpu_iowait_ticks\":" << host_cpu.iowait
             << ",\"host_cpu_irq_ticks\":" << host_cpu.irq
             << ",\"host_cpu_softirq_ticks\":" << host_cpu.softirq
             << ",\"host_cpu_steal_ticks\":" << host_cpu.steal
             << ",\"host_cpu_total_ticks\":" << host_cpu.total;
      if (!application_metrics.empty()) {
        output << ",\"application_metrics_json\":\""
               << escape_json(application_metrics) << '\"';
      }
      output << '}';
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
    const fs::path dir =
        fs::path("/tmp/mini-drop-native") / task.id / task.task_attempt_id;
    fs::create_directories(dir);
    const fs::path windows_dir = dir / "windows";
    fs::create_directories(windows_dir);
    const fs::path summary_path = dir / "continuous-summary.json";
    const fs::path archive_path = dir / "continuous-perf.tar";
    ProcessGroupRunner runner(config, stop);
    const int window_seconds = std::max(1, std::min(900,
        task.window_seconds > 0 ? task.window_seconds : 60));
    const long long max_bytes =
        static_cast<long long>(std::max(1, task.max_output_mb)) * 1024LL * 1024LL;
    const long long campaign_start = static_cast<long long>(std::time(nullptr));
    long long output_bytes = 0;
    int elapsed = 0;
    int sentinel_wait_seconds = 0;
    int window_index = 0;
    bool quota_reached = false;
    std::vector<std::string> window_json;

    // A non-zero trigger means "wait until the target is hot".  Reading
    // /proc/<pid>/stat is deliberately side-effect free; the window still uses
    // perf and therefore retains the normal perf_event capability gate.
    if (task.trigger_cpu_percent > 0) {
      const auto sentinel_start = std::chrono::steady_clock::now();
      int consecutive = 0;
      const int required = std::max(1, task.trigger_consecutive_samples);
      const int wait_limit = std::min(task.duration,
          task.trigger_wait_seconds > 0 ? task.trigger_wait_seconds : task.duration);
      long long previous_ticks = -1;
      long long previous_total = -1;
      for (int waited = 0; waited < wait_limit && consecutive < required; ++waited) {
        if (stop.load() || cancel.load()) {
          result.error = "continuous perf cancelled while waiting for sentinel";
          return result;
        }
        const std::string proc_stat = read_text(
            fs::path("/proc") / std::to_string(task.pid) / "stat");
        const std::string cpu_stat = read_text("/proc/stat");
        const auto close = proc_stat.rfind(')');
        long long process_ticks = 0;
        if (close != std::string::npos) {
          std::istringstream fields(proc_stat.substr(close + 2));
          std::string token;
          for (int index = 0; fields >> token; ++index) {
            if (index == 11 || index == 12) {
              long long ticks = 0;
              if (!parse_non_negative_integer(token, ticks)) {
                result.error = "CPU sentinel could not parse target process counters";
                return result;
              }
              process_ticks += ticks;
            }
            if (index > 12) break;
          }
        }
        std::istringstream cpu_line(cpu_stat.substr(0, cpu_stat.find('\n')));
        std::string cpu_name;
        long long value = 0, total_ticks = 0;
        cpu_line >> cpu_name;
        while (cpu_line >> value) total_ticks += value;
        int cpu_count = 0;
        std::istringstream cpu_lines(cpu_stat);
        std::string line;
        while (std::getline(cpu_lines, line)) {
          if (line.size() > 3 && line.rfind("cpu", 0) == 0 &&
              std::isdigit(static_cast<unsigned char>(line[3]))) ++cpu_count;
        }
        if (previous_ticks >= 0 && total_ticks > previous_total) {
          const double cpu_percent = 100.0 * std::max(1, cpu_count) *
              static_cast<double>(process_ticks - previous_ticks) /
              static_cast<double>(total_ticks - previous_total);
          consecutive = cpu_percent >= task.trigger_cpu_percent
              ? consecutive + 1 : 0;
        }
        previous_ticks = process_ticks;
        previous_total = total_ticks;
        if (consecutive < required) std::this_thread::sleep_for(1s);
      }
      if (consecutive < required) {
        result.error = "CPU sentinel did not reach the configured threshold";
        return result;
      }
      sentinel_wait_seconds = static_cast<int>(std::chrono::duration_cast<
          std::chrono::seconds>(std::chrono::steady_clock::now() - sentinel_start).count());
    }

    // duration is the total Task budget.  Sentinel waiting therefore consumes
    // part of the budget instead of silently exceeding the signed timeout.
    const int profile_budget = task.duration - sentinel_wait_seconds;
    if (profile_budget < 1) {
      result.error = "CPU sentinel left no time in the task budget for profiling";
      return result;
    }
    while (elapsed < profile_budget) {
      if (stop.load() || cancel.load()) {
        result.error = "continuous perf cancelled";
        return result;
      }
      const int segment = std::min(window_seconds, profile_budget - elapsed);
      const long long start_ts = static_cast<long long>(std::time(nullptr));
      const std::string stem = "window-" + std::to_string(window_index);
      const fs::path perf_data = windows_dir / (stem + ".data");
      const fs::path stderr_path = windows_dir / (stem + ".stderr");
      std::vector<std::string> command{
          "perf", "record", "--all-user", "-F", std::to_string(task.hz), "-g",
          "--call-graph", task.callgraph.empty() ? "fp" : task.callgraph};
      if (!task.event.empty()) {
        command.push_back("-e");
        command.push_back(task.event);
      }
      command.insert(command.end(), {"-p", std::to_string(task.pid), "-o",
          perf_data.string(), "--", "sleep", std::to_string(segment)});
      const CommandResult command_result = runner.run(
          command, segment + 30, stderr_path, cancel);
      const long long end_ts = static_cast<long long>(std::time(nullptr));
      if (command_result.cancelled || command_result.timed_out ||
          command_result.exit_code != 0 || !fs::exists(perf_data)) {
        result.error = command_result.cancelled ? "continuous perf cancelled" :
            command_result.timed_out ? "continuous perf window timed out" :
            "continuous perf window failed: " + read_text(stderr_path).substr(0, 300);
        return result;
      }
      const auto size = static_cast<long long>(fs::file_size(perf_data));
      output_bytes += size;
      std::ostringstream window;
      window << "{\"window_index\":" << window_index
             << ",\"start_ts\":" << start_ts << ",\"end_ts\":" << end_ts
             << ",\"duration_seconds\":" << segment
             << ",\"size_bytes\":" << size << ",\"ok\":true} ";
      window_json.push_back(window.str());
      elapsed += segment;
      ++window_index;
      if (output_bytes >= max_bytes) {
        quota_reached = true;
        break;
      }
    }

    std::ostringstream windows;
    for (std::size_t index = 0; index < window_json.size(); ++index) {
      if (index) windows << ',';
      windows << window_json[index];
    }
    std::ofstream summary(summary_path);
    summary << "{\"schema_version\":\"continuous_perf.v2\",\"pid\":" << task.pid
            << ",\"campaign_start_ts\":" << campaign_start
            << ",\"requested_duration_seconds\":" << task.duration
            << ",\"sentinel_wait_seconds\":" << sentinel_wait_seconds
            << ",\"campaign_duration_seconds\":" << (sentinel_wait_seconds + elapsed)
            << ",\"captured_duration_seconds\":" << elapsed
            << ",\"window_seconds\":" << window_seconds
            << ",\"retention_tier\":\"" << escape_json(
                task.retention_tier.empty() ? "standard" : task.retention_tier)
            << "\",\"max_output_bytes\":" << max_bytes
            << ",\"output_bytes\":" << output_bytes
            << ",\"quota_reached\":" << (quota_reached ? "true" : "false")
            << ",\"windows\":[" << windows.str() << "]}";
    summary.close();

    const fs::path tar_stderr = dir / "continuous-tar.stderr";
    const CommandResult tar_result = runner.run(
        {"tar", "-cf", archive_path.string(), "-C", dir.string(), "windows"},
        120, tar_stderr, cancel);
    if (tar_result.exit_code != 0 || !fs::exists(archive_path)) {
      result.error = "failed to package continuous windows: " +
          read_text(tar_stderr).substr(0, 300);
      return result;
    }

    const std::string archive_key = authorized_object_key(task, "continuous-perf.tar");
    const std::string summary_key = authorized_object_key(task, "continuous-summary.json");
    if (archive_key.empty() || summary_key.empty()) {
      result.error = "missing exact upload target for continuous profile outputs";
      return result;
    }
    const std::string archive_digest = sha256_file(archive_path);
    const std::string summary_digest = sha256_file(summary_path);
    if (archive_digest.empty() || summary_digest.empty()) {
      result.error = "failed to compute continuous artifact SHA-256";
      return result;
    }
    if (!upload_artifact(task, archive_path, archive_key, result.error) ||
        !upload_artifact(task, summary_path, summary_key, result.error)) return result;
    const auto archive_size = fs::file_size(archive_path);
    const auto summary_size = fs::file_size(summary_path);
    std::ostringstream artifacts;
    artifacts << "[{\"artifact_type\":\"continuous_bundle\",\"filename\":\"continuous-perf.tar\","
              << "\"bucket\":\"" << escape_json(config.minio_bucket) << "\",\"object_key\":\""
              << escape_json(archive_key) << "\",\"content_type\":\"application/x-tar\",\"size_bytes\":"
              << archive_size << ",\"sha256\":\"" << archive_digest << "\",\"metadata\":{"
              << "\"schema_version\":\"continuous_perf.v2\",\"window_count\":" << window_index
              << ",\"retention_tier\":\"" << escape_json(task.retention_tier.empty() ? "standard" : task.retention_tier)
              << "\",\"quota_reached\":" << (quota_reached ? "true" : "false") << "}},"
              << "{\"artifact_type\":\"continuous_summary\",\"filename\":\"continuous-summary.json\","
              << "\"bucket\":\"" << escape_json(config.minio_bucket) << "\",\"object_key\":\""
              << escape_json(summary_key) << "\",\"content_type\":\"application/json\",\"size_bytes\":"
              << summary_size << ",\"sha256\":\"" << summary_digest << "\",\"metadata\":{"
              << "\"schema_version\":\"continuous_perf.v2\",\"window_seconds\":" << window_seconds
              << ",\"retention_tier\":\"" << escape_json(task.retention_tier.empty() ? "standard" : task.retention_tier)
              << "\",\"quota_reached\":" << (quota_reached ? "true" : "false")
              << ",\"windows\":[" << windows.str() << "]}}]";
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
