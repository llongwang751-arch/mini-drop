#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace mini_drop_native {

constexpr std::size_t kMaxProcessCandidates = 256;
constexpr std::size_t kMaxProcessIdentityLength = 256;
constexpr std::size_t kMaxProcessHintLength = 256;
constexpr std::size_t kMaxCollectorCapabilities = 16;
constexpr std::size_t kMaxCollectorCapabilityLength = 64;
constexpr std::size_t kMaxProcessSnapshotErrorLength = 256;

struct ProcessCandidate {
  std::uint32_t pid = 0;
  std::uint64_t process_start_ticks = 0;
  std::uint64_t pid_namespace_inode = 0;
  std::uint32_t namespace_pid = 0;
  std::string comm;
  std::string executable_identity;
  std::string cgroup;
  std::string service_hint;
  std::string instance_hint;
  std::vector<std::string> collector_capabilities;
};

struct ProcessSnapshot {
  std::vector<ProcessCandidate> candidates;
  bool complete = false;
  bool truncated = false;
  std::string boot_id;
  std::uint64_t generation = 0;
  std::uint64_t observed_at_unix_ms = 0;
  std::string error;
};

// A successful scan has complete=true, including when no candidates are found.
// A truncated scan has truncated=true and complete=false. A failed scan has a
// bounded non-empty error and complete=false. Processes descended from self_pid
// are excluded because they are collector processes owned by this Agent.
ProcessSnapshot collect_process_snapshot(
    const std::vector<std::string>& available_collectors,
    const std::filesystem::path& proc_root = "/proc",
    std::size_t max_candidates = kMaxProcessCandidates,
    std::uint32_t self_pid = 0);

}  // namespace mini_drop_native
