// 采集器公共接口：RunnerContext承载已校验输入，RunnerOutput只描述本次产生的制品。
// 统一生命周期让不同语言/内核工具复用取消和预算机制；Registry负责能力发现与固定实现选择。
// 这里的Runner是执行采集工具的适配器，与调用DeepSeek的AgentScope智能体职责分离。

#pragma once
#include "drop/catalog.hpp"
#include "drop/process.hpp"
#include "drop/target.hpp"
#include <filesystem>
#include <functional>
#include <map>
#include <memory>
#include <nlohmann/json.hpp>

namespace dropcore {
class ProfilerExecutionFailure : public std::runtime_error {
public:
  explicit ProfilerExecutionFailure(int code,
                                    std::string category = "RUNNER_FAILED")
      : std::runtime_error(
            category == "PROFILER_PERMISSION_DENIED"
                ? "Trace attach denied by host or container policy; this "
                  "category "
                  "is unavailable under the current permissions"
            : category == "TRACE_ATTACH_UNAVAILABLE"
                ? "Trace attachment is unavailable under the current "
                  "kernel or read-only tracing policy"
            : category == "RUNNER_PROCESS_LIMIT"
                ? "Collector exceeded its owned process/thread budget or "
                  "bounded process accounting was unavailable"
            : category == "BOLT_TARGET_MAPPING_UNVERIFIED"
                ? "BOLT could not verify the recorded target PID, ELF "
                  "mapping identity and build-id"
            : category == "BOLT_BINARY_SAMPLES_UNAVAILABLE"
                ? "BOLT could not match samples to the target ELF; verify "
                  "the build identity and collect enough target user CPU work"
            : category == "BOLT_PERF_CONVERSION_FAILED"
                ? "BOLT could not read the captured perf data with the "
                  "configured perf executable"
            : category == "BOLT_AGGREGATION_FAILED"
                ? "BOLT rejected aggregation of the captured target profile"
                : "External profiler failed; verify target runtime and "
                  "permissions"),
        exit_code(code), error_code(std::move(category)) {}
  int exit_code;
  std::string error_code;
};
// 对外只返回固定错误分类，不回传诊断输入或其中包含的目标路径。
std::string classify_trace_failure(const std::string &diagnostic);
// BOLT同样只输出固定分类，原始工具诊断和目标路径不能泄漏到调用方。
std::string classify_bolt_failure(const std::string &diagnostic);
// 窄范围通配符必须恰好解析到一个探针后才允许附加，避免误采多个位置。
bool exact_allocator_probe_listing(const std::string &listing,
                                   const std::string &library,
                                   const std::string &symbol);
struct RunnerContext {
  std::string kind;
  ProcessIdentity target;
  nlohmann::json parameters = nlohmann::json::object(),
                 options = nlohmann::json::object();
  std::filesystem::path directory;
  // 这是受信的本地采集配置，不能从Agent IPC输入中反序列化获得。
  // 协作采集程序只能看到这个有界RAW目录；恢复记录、日志和
  // 中间文件仍由helper或普通Agent私有持有，不能共享整个工作目录。
  std::filesystem::path output_directory;
  bool java_cpu_timer = false;
  int duration_seconds = 10, frequency_hz = 99;
  int protocol_version = 2;
  std::uint64_t max_file_bytes = 33554432;
  ProcessLimits limits;
  std::function<bool()> canceled = [] { return false; };
};
struct RunnerOutput {
  std::filesystem::path path;
  std::string name, format, content_type;
  nlohmann::json metadata = nlohmann::json::object();
};
struct ToolInfo {
  std::string name, path, sha256, version, reason;
  std::map<std::string, std::string> support_files;
  bool available = false;
};
// 等待协作采集程序及压缩程序关闭输出文件描述符，再认定制品可读取。
// 仅到达计时器终点或短时间内文件大小不变，都不足以证明写入已经完成。
void wait_for_cooperative_output_close(
    const RunnerContext &,
    std::chrono::milliseconds timeout = std::chrono::seconds(10));
class Runner {
public:
  virtual ~Runner() = default;
  virtual void Validate(const RunnerContext &) const = 0;
  virtual void Prepare(const RunnerContext &) = 0;
  virtual void Start(const RunnerContext &) = 0;
  virtual bool Poll() = 0;
  virtual void Stop() noexcept = 0;
  virtual RunnerOutput Collect() = 0;
  virtual void Cleanup() noexcept = 0;
};
using RunnerAction = std::function<void(const RunnerContext &)>;
std::unique_ptr<Runner>
    make_managed_runner(RunnerAction, RunnerAction,
                        std::function<RunnerOutput(const RunnerContext &)>);
class RunnerRegistry {
public:
  RunnerRegistry(const Catalog &, std::string scratch, std::string perf_path);
  bool available(const std::string &) const;
  const ToolInfo &tool(const std::string &) const;
  nlohmann::json capabilities() const;
  RunnerOutput collect(const RunnerContext &) const;
  std::vector<std::string> argv(const RunnerContext &) const;

private:
  RunnerOutput collect_impl(const RunnerContext &) const;
  const Catalog &catalog_;
  std::map<std::string, ToolInfo> tools_;
  std::map<std::string, nlohmann::json> capabilities_;
  std::map<std::string, std::function<std::unique_ptr<Runner>()>> runners_;
};
std::vector<std::string>
build_runner_argv(const RunnerContext &,
                  const std::map<std::string, std::string> &tools);
void download_pprof(const RunnerContext &, const std::filesystem::path &output);
nlohmann::json resource_sample(int pid);
nlohmann::json bounded_kernel_snapshot(const std::string &,
                                       bool truncated = false);
} // 结束 dropcore 命名空间
