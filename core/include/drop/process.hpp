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
  // Concurrent tasks (threads included) in the owned tool process group.
  // RLIMIT_NPROC counts the shared host UID, including unrelated containers.
  // The poll enforces this budget; deployment cgroup pids.max is the hard cap.
  unsigned int processes = 32;
  // Only trusted tools may use an owner-controlled directory for nested tools.
  std::string executable_path_prefix;
  // Public tool integrity metadata for the trusted nested GDB launcher only.
  // Selected locally by Runner policy, never accepted through helper IPC.
  std::string nested_tool_manifest_path;
  // Locally pinned numeric target identity for the GDB launcher, never user
  // IPC.
  std::string nested_tool_target_identity;
  // Empty preserves the inherited mask; otherwise only an allowed CPU subset.
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
} // namespace dropcore
