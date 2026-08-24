#include "process_snapshot.h"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using mini_drop_native::ProcessSnapshot;
using mini_drop_native::collect_process_snapshot;

namespace {

class TemporaryProc {
 public:
  TemporaryProc() {
    path_ = fs::temp_directory_path() /
        ("mini-drop-process-snapshot-test-" + std::to_string(
            std::chrono::steady_clock::now().time_since_epoch().count()));
    fs::create_directories(path_ / "sys/kernel/random");
    write_file(path_ / "sys/kernel/random/boot_id", "boot-test-123\n");
  }

  ~TemporaryProc() { fs::remove_all(path_); }

  const fs::path& path() const { return path_; }

  void add_process(
      std::uint32_t pid,
      std::uint32_t parent_pid,
      std::uint64_t start_ticks,
      std::uint64_t namespace_inode,
      std::uint32_t namespace_pid,
      const std::string& comm = "candidate",
      const std::string& executable = "/usr/bin/candidate",
      const std::string& cgroup = "0::/system.slice/candidate.service") {
    const fs::path root = path_ / std::to_string(pid);
    fs::create_directories(root / "ns");
    write_file(root / "stat", make_stat(pid, parent_pid, start_ticks));
    write_file(root / "status",
               "Name:\t" + comm + "\nNSpid:\t" + std::to_string(pid) + "\t" +
                   std::to_string(namespace_pid) + "\n");
    write_file(root / "comm", comm + "\n");
    write_file(root / "cgroup", cgroup + "\n");
    create_link(executable, root / "exe");
    create_link("pid:[" + std::to_string(namespace_inode) + "]", root / "ns/pid");
  }

  void remove_boot_id() { fs::remove(path_ / "sys/kernel/random/boot_id"); }

 private:
  static void write_file(const fs::path& path, const std::string& value) {
    std::ofstream output(path, std::ios::binary);
    if (!output) throw std::runtime_error("cannot create fixture file");
    output << value;
  }

  static void create_link(const std::string& target, const fs::path& path) {
    std::error_code error;
    fs::create_symlink(target, path, error);
    if (error) throw std::runtime_error("cannot create fixture symlink: " + error.message());
  }

  static std::string make_stat(
      std::uint32_t pid, std::uint32_t parent_pid, std::uint64_t start_ticks) {
    // Fields 3..22: state, ppid, then zeroes through starttime.
    return std::to_string(pid) + " (fixture process) S " +
        std::to_string(parent_pid) +
        " 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 " +
        std::to_string(start_ticks) + "\n";
  }

  fs::path path_;
};

void require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

void test_complete_snapshot_has_bounded_metadata() {
  TemporaryProc proc;
  proc.add_process(
      200, 1, 9876, 4026532000ULL, 20,
      std::string(300, 'x') + "\nunsafe", "/opt/apps/service-binary",
      "0::/system.slice/example.service/docker-container123.scope");
  std::vector<std::string> capabilities(20, std::string(100, 'c'));

  const ProcessSnapshot snapshot =
      collect_process_snapshot(capabilities, proc.path(), 256, 100);
  require(snapshot.complete, "successful snapshot was not complete");
  require(!snapshot.truncated, "successful snapshot was truncated");
  require(snapshot.error.empty(), "successful snapshot has an error");
  require(snapshot.boot_id == "boot-test-123", "boot id mismatch");
  require(snapshot.generation > 0, "generation was not assigned");
  require(snapshot.observed_at_unix_ms > 0, "observation time was not assigned");
  require(snapshot.candidates.size() == 1, "candidate count mismatch");

  const auto& candidate = snapshot.candidates.front();
  require(candidate.pid == 200, "pid mismatch");
  require(candidate.process_start_ticks == 9876, "start ticks mismatch");
  require(candidate.pid_namespace_inode == 4026532000ULL, "namespace inode mismatch");
  require(candidate.namespace_pid == 20, "namespace pid mismatch");
  require(candidate.comm.size() <= mini_drop_native::kMaxProcessIdentityLength,
          "comm was not bounded");
  require(candidate.comm.find('\n') == std::string::npos, "comm control text retained");
  require(candidate.executable_identity == "service-binary", "executable mismatch");
  require(candidate.cgroup.size() <= mini_drop_native::kMaxProcessHintLength,
          "cgroup was not bounded");
  require(candidate.service_hint == "example.service", "service hint mismatch");
  require(candidate.instance_hint == "docker-container123.scope", "instance hint mismatch");
  require(candidate.collector_capabilities.size() ==
              mini_drop_native::kMaxCollectorCapabilities,
          "capability count was not bounded");
  for (const auto& capability : candidate.collector_capabilities) {
    require(capability.size() <= mini_drop_native::kMaxCollectorCapabilityLength,
            "capability text was not bounded");
  }
}

void test_empty_snapshot_is_complete() {
  TemporaryProc proc;
  const ProcessSnapshot snapshot = collect_process_snapshot({}, proc.path(), 256, 100);
  require(snapshot.complete, "empty successful snapshot was not complete");
  require(!snapshot.truncated, "empty snapshot was truncated");
  require(snapshot.error.empty(), "empty snapshot has an error");
  require(snapshot.candidates.empty(), "empty snapshot has candidates");
}

void test_agent_and_collector_descendants_are_excluded() {
  TemporaryProc proc;
  proc.add_process(100, 1, 1000, 4026531000ULL, 100, "agent");
  proc.add_process(110, 100, 1100, 4026531000ULL, 110, "collector");
  proc.add_process(111, 110, 1110, 4026531000ULL, 111, "collector-helper");
  proc.add_process(200, 1, 2000, 4026532000ULL, 200, "workload");

  const ProcessSnapshot snapshot = collect_process_snapshot({}, proc.path(), 256, 100);
  require(snapshot.complete, "descendant exclusion scan failed");
  require(snapshot.candidates.size() == 1, "agent descendant was included");
  require(snapshot.candidates.front().pid == 200, "workload candidate missing");
}

void test_incomplete_immutable_identity_is_rejected() {
  TemporaryProc proc;
  proc.add_process(200, 1, 2000, 1, 200);
  proc.add_process(201, 1, 2010, 4026532001ULL, 201);
  fs::remove(proc.path() / "200/ns/pid");
  fs::remove(proc.path() / "201/status");
  proc.add_process(202, 1, 2020, 4026532002ULL, 202);
  fs::remove(proc.path() / "202/stat");

  const ProcessSnapshot snapshot = collect_process_snapshot({}, proc.path(), 256, 100);
  require(snapshot.complete, "identity rejection made scan fail");
  require(snapshot.candidates.empty(), "incomplete immutable identity was included");
}

void test_truncated_snapshot_is_distinct() {
  TemporaryProc proc;
  proc.add_process(200, 1, 2000, 4026532000ULL, 200);
  proc.add_process(201, 1, 2010, 4026532001ULL, 201);

  const ProcessSnapshot snapshot = collect_process_snapshot({}, proc.path(), 1, 100);
  require(!snapshot.complete, "truncated snapshot marked complete");
  require(snapshot.truncated, "candidate limit did not mark truncation");
  require(snapshot.error.empty(), "truncated snapshot was marked failed");
  require(snapshot.candidates.size() == 1, "truncated candidate count mismatch");
}

void test_failed_snapshot_is_distinct_and_bounded() {
  TemporaryProc proc;
  proc.remove_boot_id();
  const ProcessSnapshot snapshot = collect_process_snapshot({}, proc.path(), 256, 100);
  require(!snapshot.complete, "failed snapshot marked complete");
  require(!snapshot.truncated, "failed snapshot marked truncated");
  require(!snapshot.error.empty(), "failed snapshot has no error");
  require(snapshot.error.size() <= mini_drop_native::kMaxProcessSnapshotErrorLength,
          "snapshot error was not bounded");
  require(snapshot.candidates.empty(), "failed snapshot has candidates");
}

}  // namespace

int main() {
  try {
    test_complete_snapshot_has_bounded_metadata();
    test_empty_snapshot_is_complete();
    test_agent_and_collector_descendants_are_excluded();
    test_incomplete_immutable_identity_is_rejected();
    test_truncated_snapshot_is_distinct();
    test_failed_snapshot_is_distinct_and_bounded();
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  return 0;
}
