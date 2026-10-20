// 子进程执行契约：argv、时间/空间/进程预算、取消回调及有界输出。
// 调用者必须先选定受信工具和目标；不能把浏览器输入直接当作命令或文件路径传入。

#pragma once

#include <chrono>
#include <cstdint>
#include <functional>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <vector>

namespace dropcore {
struct ProcessResult {
  int exit_code = -1;
  bool timed_out = false;
  bool canceled = false;
  bool process_limit_exceeded = false;
  std::string output;
};
struct ProcessLimits {
  std::uint64_t file_bytes = 64ULL * 1024 * 1024;
  std::uint64_t address_space_bytes = 8ULL * 1024 * 1024 * 1024;
  unsigned int cpu_seconds = 120;
  // 预算统计当前工具进程组中的并发任务，Linux线程也计入，不能只数子进程。
  // RLIMIT_NPROC按共享宿主UID计数，会把无关容器也算进来，不适合作为单次采集预算。
  // 本层通过轮询执行任务预算；部署层cgroup的pids.max提供额外硬上限。
  unsigned int processes = 32;
  // 只有受信工具可以为嵌套工具使用调用者持有的目录，外部请求不能任意指定工作目录。
  std::string executable_path_prefix;
  // 仅供受信的嵌套GDB启动器核验工具完整性，这里不包含私密诊断内容。
  // 由Runner本地策略选择，不能从helper IPC请求中接受此配置。
  std::string nested_tool_manifest_path;
  // GDB启动器使用本地固定的数字目标身份，不能由用户通过
  // IPC自行声明并绕过身份校验。
  std::string nested_tool_target_identity;
  // 空集合表示继承原CPU亲和性；非空时只允许缩小到已授权的CPU子集。
  std::vector<unsigned> cpu_affinity;
};

std::vector<unsigned> parse_cpu_affinity(const std::string &value);

ProcessResult execute(
    const std::vector<std::string> &argv, const std::string &output_path,
    std::chrono::seconds timeout,
    const std::function<bool()> &is_canceled = [] { return false; },
    const ProcessLimits &limits = {});
std::string read_file(const std::string &path, std::size_t limit = 1024 * 1024);
std::string secret(const char *name, const std::string &fallback = "");
struct LogContext {
  std::string request_id, agent_id, stage, level;
  std::optional<std::int64_t> task_id, attempt_id;
  std::optional<double> duration_ms;
};
nlohmann::json log_record(const std::string &service,
                          const std::string &message,
                          const std::string &code = "",
                          const LogContext &context = {});
void log(const std::string &service, const std::string &message,
         const std::string &task_id = "", const std::string &code = "",
         const LogContext &context = {});
void log_configuration(const std::string &service);
} // 结束 dropcore 命名空间
