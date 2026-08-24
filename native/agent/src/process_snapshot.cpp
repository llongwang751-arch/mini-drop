#include "process_snapshot.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <fstream>
#include <limits>
#include <sstream>
#include <system_error>
#include <unordered_map>
#include <unordered_set>

#include <unistd.h>

namespace fs = std::filesystem;

namespace mini_drop_native {
namespace {

std::atomic<std::uint64_t> g_observation_generation{0};

std::string bounded_text(std::string value, std::size_t limit) {
  value.erase(std::remove_if(value.begin(), value.end(), [](unsigned char ch) {
    return ch == 0 || ch == '\n' || ch == '\r' || ch < 0x20 || ch == 0x7f;
  }), value.end());
  if (value.size() > limit) value.resize(limit);
  return value;
}

bool numeric_name(const std::string& value) {
  return !value.empty() && std::all_of(value.begin(), value.end(), [](unsigned char ch) {
    return std::isdigit(ch) != 0;
  });
}

bool parse_u32(const std::string& value, std::uint32_t& result) {
  try {
    std::size_t parsed = 0;
    const std::uint64_t raw = std::stoull(value, &parsed);
    if (parsed != value.size() || raw == 0 ||
        raw > std::numeric_limits<std::uint32_t>::max()) {
      return false;
    }
    result = static_cast<std::uint32_t>(raw);
    return true;
  } catch (...) {
    return false;
  }
}

std::string read_first_line(const fs::path& path, std::size_t limit) {
  std::ifstream input(path);
  std::string line;
  if (!std::getline(input, line)) return {};
  return bounded_text(std::move(line), limit);
}

std::uint64_t inode_from_link(const fs::path& path) {
  std::error_code error;
  const std::string link = fs::read_symlink(path, error).string();
  if (error) return 0;
  const auto left = link.find('[');
  const auto right = link.find(']', left == std::string::npos ? 0 : left + 1);
  if (left == std::string::npos || right == std::string::npos) return 0;
  try {
    std::size_t parsed = 0;
    const std::string value = link.substr(left + 1, right - left - 1);
    const std::uint64_t inode = std::stoull(value, &parsed);
    return parsed == value.size() ? inode : 0;
  } catch (...) {
    return 0;
  }
}

bool parse_stat(
    const fs::path& path,
    std::uint32_t& parent_pid,
    std::uint64_t& start_ticks) {
  std::ifstream input(path);
  std::string line;
  if (!std::getline(input, line)) return false;
  const auto close = line.rfind(')');
  if (close == std::string::npos || close + 2 >= line.size()) return false;
  std::istringstream fields(line.substr(close + 2));
  std::string value;
  // The substring begins at field 3. PPID is field 4; start time is field 22.
  for (int field = 3; field <= 22; ++field) {
    if (!(fields >> value)) return false;
    if (field == 4) {
      try {
        std::size_t parsed = 0;
        const std::uint64_t raw = std::stoull(value, &parsed);
        if (parsed != value.size() ||
            raw > std::numeric_limits<std::uint32_t>::max()) {
          return false;
        }
        parent_pid = static_cast<std::uint32_t>(raw);
      } catch (...) {
        return false;
      }
    } else if (field == 22) {
      try {
        std::size_t parsed = 0;
        start_ticks = std::stoull(value, &parsed);
        if (parsed != value.size()) return false;
      } catch (...) {
        return false;
      }
    }
  }
  return start_ticks != 0;
}

std::uint32_t namespace_pid(const fs::path& status_path) {
  std::ifstream input(status_path);
  std::string line;
  while (std::getline(input, line)) {
    if (line.rfind("NSpid:", 0) != 0) continue;
    std::istringstream values(line.substr(6));
    std::uint64_t item = 0;
    std::uint64_t last = 0;
    bool found = false;
    while (values >> item) {
      last = item;
      found = true;
    }
    if (found && last > 0 && last <= std::numeric_limits<std::uint32_t>::max()) {
      return static_cast<std::uint32_t>(last);
    }
    return 0;
  }
  return 0;
}

std::string executable_identity(const fs::path& exe_link) {
  std::error_code error;
  fs::path target = fs::read_symlink(exe_link, error);
  if (error) return {};
  std::string identity = target.filename().string();
  if (identity.empty()) identity = target.string();
  return bounded_text(std::move(identity), kMaxProcessIdentityLength);
}

std::string cgroup_hint(const fs::path& path) {
  std::ifstream input(path);
  std::string line;
  std::string fallback;
  while (std::getline(input, line)) {
    const auto first_colon = line.find(':');
    if (first_colon == std::string::npos) continue;
    const auto second_colon = line.find(':', first_colon + 1);
    if (second_colon == std::string::npos) continue;
    std::string group = line.substr(second_colon + 1);
    if (group.empty() || group == "/") continue;
    if (fallback.empty()) fallback = group;
    if (group.find(".service") != std::string::npos ||
        group.find("docker") != std::string::npos ||
        group.find("kubepods") != std::string::npos ||
        group.find("containerd") != std::string::npos) {
      return bounded_text(std::move(group), kMaxProcessHintLength);
    }
  }
  return bounded_text(std::move(fallback), kMaxProcessHintLength);
}

std::string service_hint_from_cgroup(const std::string& cgroup) {
  std::size_t end = 0;
  while (end < cgroup.size()) {
    const std::size_t start = cgroup.find_first_not_of('/', end);
    if (start == std::string::npos) break;
    end = cgroup.find('/', start);
    const std::string part = cgroup.substr(
        start, end == std::string::npos ? std::string::npos : end - start);
    if (part.size() > 8 && part.compare(part.size() - 8, 8, ".service") == 0) {
      return bounded_text(part, kMaxProcessHintLength);
    }
    if (end == std::string::npos) break;
  }
  return {};
}

std::string instance_hint_from_cgroup(const std::string& cgroup) {
  std::string fallback;
  std::size_t end = 0;
  while (end < cgroup.size()) {
    const std::size_t start = cgroup.find_first_not_of('/', end);
    if (start == std::string::npos) break;
    end = cgroup.find('/', start);
    const std::string part = cgroup.substr(
        start, end == std::string::npos ? std::string::npos : end - start);
    if (!part.empty()) fallback = part;
    if (part.find("docker-") == 0 || part.find("cri-containerd-") == 0 ||
        part.find("crio-") == 0) {
      return bounded_text(part, kMaxProcessHintLength);
    }
    if (end == std::string::npos) break;
  }
  return bounded_text(std::move(fallback), kMaxProcessHintLength);
}

std::vector<std::string> bounded_capabilities(
    const std::vector<std::string>& capabilities) {
  std::vector<std::string> result;
  result.reserve(std::min(capabilities.size(), kMaxCollectorCapabilities));
  for (const auto& capability : capabilities) {
    if (result.size() == kMaxCollectorCapabilities) break;
    const std::string safe = bounded_text(capability, kMaxCollectorCapabilityLength);
    if (!safe.empty()) result.push_back(safe);
  }
  return result;
}

bool is_agent_process(
    std::uint32_t pid,
    std::uint32_t self_pid,
    const std::unordered_map<std::uint32_t, std::uint32_t>& parents) {
  std::unordered_set<std::uint32_t> visited;
  while (pid != 0 && visited.insert(pid).second) {
    if (pid == self_pid) return true;
    const auto parent = parents.find(pid);
    if (parent == parents.end()) return false;
    pid = parent->second;
  }
  return false;
}

}  // namespace

ProcessSnapshot collect_process_snapshot(
    const std::vector<std::string>& available_collectors,
    const fs::path& proc_root,
    std::size_t max_candidates,
    std::uint32_t self_pid) {
  ProcessSnapshot snapshot;
  snapshot.generation = ++g_observation_generation;
  snapshot.observed_at_unix_ms = static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count());
  snapshot.boot_id = read_first_line(
      proc_root / "sys/kernel/random/boot_id", kMaxProcessIdentityLength);
  if (snapshot.boot_id.empty()) {
    snapshot.error = "cannot read boot_id";
    return snapshot;
  }
  if (self_pid == 0) self_pid = static_cast<std::uint32_t>(::getpid());
  max_candidates = std::min(max_candidates, kMaxProcessCandidates);
  const auto capabilities = bounded_capabilities(available_collectors);

  std::error_code error;
  fs::directory_iterator entries(proc_root, error);
  if (error) {
    snapshot.error = bounded_text(
        "cannot scan proc root: " + error.message(), kMaxProcessSnapshotErrorLength);
    return snapshot;
  }

  std::vector<fs::path> process_roots;
  for (fs::directory_iterator end; entries != end;) {
    if (error) {
      snapshot.error = "proc directory iteration failed";
      return snapshot;
    }
    if (numeric_name(entries->path().filename().string())) {
      process_roots.push_back(entries->path());
    }
    entries.increment(error);
  }
  if (error) {
    snapshot.error = "proc directory iteration failed";
    return snapshot;
  }
  std::sort(process_roots.begin(), process_roots.end(), [](const fs::path& left,
                                                            const fs::path& right) {
    std::uint32_t left_pid = 0;
    std::uint32_t right_pid = 0;
    parse_u32(left.filename().string(), left_pid);
    parse_u32(right.filename().string(), right_pid);
    return left_pid < right_pid;
  });

  struct ObservedProcess {
    fs::path root;
    std::uint32_t pid = 0;
    std::uint32_t parent_pid = 0;
    std::uint64_t start_ticks = 0;
  };
  std::vector<ObservedProcess> observed;
  std::unordered_map<std::uint32_t, std::uint32_t> parents;
  observed.reserve(process_roots.size());
  for (const auto& process_root : process_roots) {
    ObservedProcess process;
    process.root = process_root;
    if (!parse_u32(process_root.filename().string(), process.pid) ||
        !parse_stat(process_root / "stat", process.parent_pid, process.start_ticks)) {
      // Processes can exit during a scan. Omit identities we cannot attest.
      continue;
    }
    parents[process.pid] = process.parent_pid;
    observed.push_back(std::move(process));
  }

  for (const auto& process : observed) {
    if (is_agent_process(process.pid, self_pid, parents)) continue;

    ProcessCandidate candidate;
    candidate.pid = process.pid;
    candidate.process_start_ticks = process.start_ticks;
    candidate.pid_namespace_inode = inode_from_link(process.root / "ns/pid");
    candidate.namespace_pid = namespace_pid(process.root / "status");
    // Never attest a candidate without all immutable identity components.
    if (candidate.process_start_ticks == 0 || candidate.pid_namespace_inode == 0 ||
        candidate.namespace_pid == 0) {
      continue;
    }
    if (snapshot.candidates.size() == max_candidates) {
      snapshot.truncated = true;
      break;
    }

    candidate.comm = read_first_line(
        process.root / "comm", kMaxProcessIdentityLength);
    candidate.executable_identity = executable_identity(process.root / "exe");
    candidate.cgroup = cgroup_hint(process.root / "cgroup");
    candidate.service_hint = service_hint_from_cgroup(candidate.cgroup);
    candidate.instance_hint = instance_hint_from_cgroup(candidate.cgroup);
    candidate.collector_capabilities = capabilities;
    snapshot.candidates.push_back(std::move(candidate));
  }

  snapshot.complete = !snapshot.truncated;
  return snapshot;
}

}  // namespace mini_drop_native
