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
// Returns only a fixed classification, never the diagnostic input or its paths.
std::string classify_trace_failure(const std::string &diagnostic);
// Fixed categories only: no raw profiler diagnostics or target paths escape.
std::string classify_bolt_failure(const std::string &diagnostic);
// Narrow wildcard attachment is permitted only after exactly-one expansion.
bool exact_allocator_probe_listing(const std::string &listing,
                                   const std::string &library,
                                   const std::string &symbol);
struct RunnerContext {
  std::string kind;
  ProcessIdentity target;
  nlohmann::json parameters = nlohmann::json::object(),
                 options = nlohmann::json::object();
  std::filesystem::path directory;
  // Trusted local collector configuration; never decoded from Agent IPC.
  // Cooperative writers see only this bounded RAW directory. Journals, logs
  // and intermediates remain private to the helper or ordinary Agent owner.
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
// Wait for cooperative writers/compression to close their output descriptors.
// A timer elapsed or a stable short size sample does not establish completion.
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
} // namespace dropcore
