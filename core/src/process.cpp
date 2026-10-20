// 受控子进程执行与公共日志工具：上层传入argv数组，本层不经shell拼接命令。
// 每次执行使用独立进程组、时间/输出/资源限制，并在取消或超限时回收自己持有的子进程。
// 同UID可能同时属于其他容器，因此不能把所有同UID进程当作当前任务的预算或清理对象。
// 公开日志采用受限上下文；读取文件也有大小上限，避免错误输入撑爆采集进程。

#include "drop/process.hpp"
#include "drop/config.hpp"

#include <fcntl.h>
#include <nlohmann/json.hpp>
#include <sched.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cerrno>
#include <charconv>
#include <cmath>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <thread>

namespace dropcore {
namespace {
// 只统计本次工具进程组的进程与线程，避免把同UID无关进程算入预算。
// /proc读取失败时拒绝继续，不能把“没数清楚”解释为“没有超限”。
bool process_group_over_budget(pid_t group, unsigned maximum) {
  unsigned scanned = 0;
  std::uint64_t tasks = 0;
  for (const auto &entry : std::filesystem::directory_iterator("/proc")) {
    const auto name = entry.path().filename().string();
    unsigned pid{};
    const auto parsed =
        std::from_chars(name.data(), name.data() + name.size(), pid);
    if (parsed.ec != std::errc{} || parsed.ptr != name.data() + name.size())
      continue;
    if (++scanned > 32768)
      throw std::runtime_error("Collector process scan exceeded its bound");
    std::ifstream input(entry.path() / "stat");
    std::string stat;
    std::getline(input, stat);
    if (!input || stat.size() > 8192)
      continue; // 无关进程或正在并发退出的进程不能被认作当前采集器持有。
    const auto end = stat.rfind(')');
    if (end == std::string::npos)
      continue;
    std::istringstream fields(stat.substr(end + 2));
    std::string state;
    pid_t parent{}, process_group{};
    if (!(fields >> state >> parent >> process_group) || process_group != group)
      continue;
    std::string field;
    // /proc/PID/stat中，第6至19字段位于线程数num_threads（第20字段）之前。
    for (unsigned index = 6; index < 20; ++index)
      fields >> field;
    std::uint64_t threads{};
    if (!(fields >> threads) || threads == 0)
      throw std::runtime_error(
          "Owned collector process has invalid task count");
    if (threads > maximum || tasks > maximum - threads)
      return true;
    tasks += threads;
  }
  return false;
}

cpu_set_t validated_cpu_affinity(const std::vector<unsigned> &cpus) {
  cpu_set_t selected;
  CPU_ZERO(&selected);
  if (cpus.empty())
    return selected;
  if (cpus.size() > 64)
    throw std::invalid_argument(
        "Collector CPU affinity exceeds CPU count limit");
  cpu_set_t allowed;
  CPU_ZERO(&allowed);
  if (sched_getaffinity(0, sizeof(allowed), &allowed) != 0)
    throw std::runtime_error("Current collector CPU affinity is unavailable");
  for (const auto cpu : cpus) {
    if (cpu >= CPU_SETSIZE || !CPU_ISSET(cpu, &allowed) ||
        CPU_ISSET(cpu, &selected))
      throw std::invalid_argument(
          "Collector CPU affinity must be a unique allowed CPU subset");
    CPU_SET(cpu, &selected);
  }
  return selected;
}
} // 结束匿名命名空间

std::vector<unsigned> parse_cpu_affinity(const std::string &value) {
  if (value.empty())
    return {};
  if (value.size() > 512)
    throw std::invalid_argument("Collector CPU affinity exceeds input limit");
  std::vector<unsigned> cpus;
  std::size_t start = 0;
  for (;;) {
    const auto delimiter = value.find(',', start);
    const auto end = delimiter == std::string::npos ? value.size() : delimiter;
    if (end == start || cpus.size() >= 64 ||
        value.find_first_not_of("0123456789", start) < end)
      throw std::invalid_argument(
          "Collector CPU affinity requires comma-separated CPU numbers");
    unsigned cpu = 0;
    const auto parsed =
        std::from_chars(value.data() + start, value.data() + end, cpu);
    if (parsed.ec != std::errc{} || parsed.ptr != value.data() + end)
      throw std::invalid_argument("Invalid collector CPU number");
    cpus.push_back(cpu);
    if (delimiter == std::string::npos)
      break;
    start = delimiter + 1;
  }
  validated_cpu_affinity(cpus);
  return cpus;
}

std::string read_file(const std::string &path, std::size_t limit) {
  std::ifstream in(path, std::ios::binary);
  if (!in)
    throw std::runtime_error("Could not read file");
  std::string data(limit, '\0');
  in.read(data.data(), static_cast<std::streamsize>(limit));
  data.resize(static_cast<std::size_t>(in.gcount()));
  return data;
}

std::string secret(const char *name, const std::string &fallback) {
  return configuration_value(name, fallback);
}
namespace {
std::mutex log_mutex;
std::string log_text(const std::string &value, std::size_t maximum) {
  std::string output;
  for (const auto character : value) {
    const auto byte = static_cast<unsigned char>(character);
    if (byte >= 32 && byte != 127)
      output += character;
    if (output.size() >= maximum)
      break;
  }
  return output;
}
nlohmann::json log_identifier(const std::string &value) {
  if (value.empty() || value.size() > 128 ||
      value.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUV"
                              "WXYZ0123456789._:-") != std::string::npos)
    return nullptr;
  return value;
}
void emit_log(const nlohmann::json &record) {
  std::lock_guard lock(log_mutex);
  std::cerr << record.dump(-1, ' ', false,
                           nlohmann::json::error_handler_t::replace)
            << '\n';
}
} // 结束匿名命名空间
nlohmann::json log_record(const std::string &service,
                          const std::string &message, const std::string &code,
                          const LogContext &context) {
  using json = nlohmann::json;
  const auto now = std::chrono::system_clock::now();
  const auto seconds = std::chrono::system_clock::to_time_t(now);
  std::tm utc{};
  gmtime_r(&seconds, &utc);
  const auto fraction = std::chrono::duration_cast<std::chrono::milliseconds>(
                            now.time_since_epoch())
                            .count() %
                        1000;
  std::ostringstream time;
  time << std::put_time(&utc, "%Y-%m-%dT%H:%M:%S") << '.' << std::setw(3)
       << std::setfill('0') << fraction << 'Z';
  auto level =
      context.level.empty() ? (code.empty() ? "info" : "error") : context.level;
  if (level != "debug" && level != "info" && level != "warning" &&
      level != "error")
    level = "error";
  const auto error = code.empty() ? json(nullptr) : log_identifier(code);
  return {{"time", time.str()},
          {"level", level},
          {"service", log_text(service, 64)},
          {"version", "2.0.0"},
          {"request_id", log_identifier(context.request_id)},
          {"task_id", context.task_id && *context.task_id > 0
                          ? json(*context.task_id)
                          : json(nullptr)},
          {"attempt_id", context.attempt_id && *context.attempt_id > 0
                             ? json(*context.attempt_id)
                             : json(nullptr)},
          {"agent_id", log_identifier(context.agent_id)},
          {"stage", log_identifier(context.stage)},
          {"duration_ms", context.duration_ms &&
                                  std::isfinite(*context.duration_ms) &&
                                  *context.duration_ms >= 0
                              ? json(*context.duration_ms)
                              : json(nullptr)},
          {"error_code", error},
          {"message", log_text(message, 1024)}};
}
void log(const std::string &service, const std::string &message,
         const std::string &task_id, const std::string &code,
         const LogContext &context) {
  auto selected = context;
  if (!selected.task_id && !task_id.empty()) {
    std::int64_t parsed{};
    const auto result = std::from_chars(
        task_id.data(), task_id.data() + task_id.size(), parsed);
    if (result.ec == std::errc{} &&
        result.ptr == task_id.data() + task_id.size() && parsed > 0)
      selected.task_id = parsed;
  }
  emit_log(log_record(service, message, code, selected));
}
void log_configuration(const std::string &service) {
  auto configuration = configuration_snapshot();
  LogContext context;
  context.stage = "startup";
  if (configuration.at("values").contains("AGENT_ID"))
    context.agent_id =
        configuration.at("values").at("AGENT_ID").get<std::string>();
  auto record =
      log_record(service, "Effective configuration loaded", "", context);
  record["configuration"] = std::move(configuration);
  emit_log(record);
}

ProcessResult execute(const std::vector<std::string> &argv,
                      const std::string &output_path,
                      std::chrono::seconds timeout,
                      const std::function<bool()> &is_canceled,
                      const ProcessLimits &limits) {
  if (argv.empty() || argv.front().empty() || argv.front().front() != '/')
    throw std::invalid_argument("Executable must be absolute");
  if (limits.processes == 0 || limits.processes > 64)
    throw std::invalid_argument("Collector process budget must be 1..64");
  for (const auto &argument : argv)
    if (argument.find('\0') != std::string::npos)
      throw std::invalid_argument("NUL in argv");
  const auto affinity = validated_cpu_affinity(limits.cpu_affinity);
  std::string search_path = "PATH=/usr/bin:/bin";
  std::string nested_manifest, nested_target;
  if (!limits.nested_tool_target_identity.empty()) {
    const auto &value = limits.nested_tool_target_identity;
    if (value.size() > 4096 || value.find('\0') != std::string::npos ||
        limits.nested_tool_manifest_path.empty())
      throw std::invalid_argument(
          "Nested tool identity must be bounded and paired with its manifest");
    nested_target = "DROP_GDB_TARGET_IDENTITY=" + value;
  }
  if (!limits.nested_tool_manifest_path.empty()) {
    const auto &path = limits.nested_tool_manifest_path;
    if (path.front() != '/' || path.size() > 4096 ||
        path.find('\0') != std::string::npos)
      throw std::invalid_argument(
          "Nested tool manifest must be an absolute bounded path");
    nested_manifest = "DROP_GDB_MANIFEST_FILE=" + path;
  }
  if (!limits.executable_path_prefix.empty()) {
    const auto &prefix = limits.executable_path_prefix;
    struct stat directory {};
    if (prefix.front() != '/' || prefix.size() > 4096 ||
        prefix.find(':') != std::string::npos ||
        prefix.find('\0') != std::string::npos ||
        stat(prefix.c_str(), &directory) != 0 || !S_ISDIR(directory.st_mode) ||
        (directory.st_mode & 0022) ||
        (directory.st_uid != 0 && directory.st_uid != getuid()))
      throw std::invalid_argument(
          "Nested tool directory must be absolute and owner controlled");
    search_path = "PATH=" + prefix + ":/usr/bin:/bin";
  }
  int fd = open(output_path.c_str(),
                O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0600);
  if (fd < 0)
    throw std::runtime_error("Could not open runner output");
  std::vector<char *> args;
  for (const auto &item : argv)
    args.push_back(const_cast<char *>(item.c_str()));
  args.push_back(nullptr);
  pid_t child = fork();
  if (child < 0) {
    close(fd);
    throw std::runtime_error("fork failed");
  }
  if (child == 0) {
    setpgid(0, 0);
    prctl(PR_SET_PDEATHSIG, SIGKILL);
    umask(0077);
    if (!limits.cpu_affinity.empty() &&
        sched_setaffinity(0, sizeof(affinity), &affinity) != 0)
      _exit(126);
    struct rlimit file_limit {
      limits.file_bytes, limits.file_bytes
    };
    struct rlimit core_limit {
      0, 0
    };
    struct rlimit cpu_limit {
      limits.cpu_seconds, limits.cpu_seconds
    };
    struct rlimit descriptor_limit {
      64, 64
    };
    struct rlimit address_limit {
      limits.address_space_bytes, limits.address_space_bytes
    };
    setrlimit(RLIMIT_FSIZE, &file_limit);
    setrlimit(RLIMIT_CORE, &core_limit);
    setrlimit(RLIMIT_CPU, &cpu_limit);
    setrlimit(RLIMIT_NOFILE, &descriptor_limit);
    setrlimit(RLIMIT_AS, &address_limit);
    dup2(fd, STDOUT_FILENO);
    dup2(fd, STDERR_FILENO);
    close(fd);

    char language[] = "LC_ALL=C";
    char *env[] = {search_path.data(), language, nullptr, nullptr, nullptr};
    unsigned position = 2;
    if (!nested_manifest.empty())
      env[position++] = nested_manifest.data();
    if (!nested_target.empty())
      env[position++] = nested_target.data();
    execve(args[0], args.data(), env);
    _exit(127);
  }
  close(fd);
  setpgid(child, child);
  ProcessResult result;
  const auto deadline = std::chrono::steady_clock::now() + timeout;
  int status = 0;
  bool terminating = false;
  auto kill_deadline = deadline;
  for (;;) {
    pid_t done = waitpid(child, &status, WNOHANG);
    if (done == child)
      break;
    if (done < 0 && errno != EINTR)
      throw std::runtime_error("waitpid failed");
    const auto now = std::chrono::steady_clock::now();
    if (!terminating) {
      try {
        result.process_limit_exceeded =
            process_group_over_budget(child, limits.processes);
      } catch (...) {
        // 有界/proc计数无法完成时按失败处理，不能跳过预算继续运行。
        result.process_limit_exceeded = true;
      }
      if (result.process_limit_exceeded) {
        kill(-child, SIGKILL);
        terminating = true;
        kill_deadline = now;
      }
    }
    if (!terminating && (is_canceled() || now > deadline)) {
      result.canceled = is_canceled();
      result.timed_out = !result.canceled;
      kill(-child, SIGTERM);
      terminating = true;
      kill_deadline = now + std::chrono::seconds(2);
    }
    if (terminating && now > kill_deadline)
      kill(-child, SIGKILL);
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
  // 当前进程组归此Runner持有，主工具退出后仍可能遗留
  // 子进程；收尾时按进程组清理，避免误杀同UID的其他工作。
  kill(-child, SIGKILL);
  result.exit_code =
      WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
  result.output = read_file(output_path, 16 * 1024);
  return result;
}
} // 结束 dropcore 命名空间
