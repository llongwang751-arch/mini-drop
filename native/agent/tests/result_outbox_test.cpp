#include "result_outbox.h"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

namespace fs = std::filesystem;
using mini_drop_native::ResultOutbox;
using mini_drop_native::TaskResult;

namespace {

class TemporaryDirectory {
 public:
  TemporaryDirectory() {
    path_ = fs::temp_directory_path() /
        ("mini-drop-outbox-test-" + std::to_string(
            std::chrono::steady_clock::now().time_since_epoch().count()));
    fs::create_directories(path_);
  }

  ~TemporaryDirectory() { fs::remove_all(path_); }

  const fs::path& path() const { return path_; }

 private:
  fs::path path_;
};

void require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

TaskResult make_result(const std::string& task_id, bool ok,
                       const std::string& error,
                       const std::string& artifact_json) {
  TaskResult result;
  result.task_id = task_id;
  result.ok = ok;
  result.error = error;
  result.artifact_json = artifact_json;
  return result;
}

void test_roundtrip_and_acknowledge() {
  TemporaryDirectory directory;
  ResultOutbox outbox(directory.path());
  const auto saved = outbox.enqueue(
      make_result("task-1", true, "", "{\"artifact\":\"raw\"}"));
  const auto entries = outbox.pending();
  require(entries.size() == 1, "roundtrip entry count");
  require(entries[0].result.task_id == "task-1", "roundtrip task id");
  require(entries[0].result.ok, "roundtrip status");
  require(entries[0].result.artifact_json == "{\"artifact\":\"raw\"}",
          "roundtrip artifact");
  outbox.acknowledge(entries[0]);
  require(outbox.pending().empty(), "acknowledged entry remains pending");
  require(!fs::exists(saved.path), "acknowledged file still exists");
}

void test_same_task_replaces_entry() {
  TemporaryDirectory directory;
  ResultOutbox outbox(directory.path());
  outbox.enqueue(make_result("task-1", false, "first", ""));
  outbox.enqueue(make_result("task-1", true, "", "second"));
  const auto entries = outbox.pending();
  require(entries.size() == 1, "same task created duplicate entries");
  require(entries[0].result.ok, "replacement status mismatch");
  require(entries[0].result.artifact_json == "second", "replacement payload mismatch");
}

void test_restart_replays_unacknowledged_entry() {
  TemporaryDirectory directory;
  {
    ResultOutbox first_process(directory.path());
    first_process.enqueue(make_result("task-restart", false, "offline", ""));
  }
  ResultOutbox restarted_process(directory.path());
  const auto entries = restarted_process.pending();
  require(entries.size() == 1, "restart did not replay entry");
  require(entries[0].result.task_id == "task-restart", "replayed task mismatch");
  require(entries[0].result.error == "offline", "replayed error mismatch");
}

void test_corrupt_entry_is_quarantined() {
  TemporaryDirectory directory;
  std::ofstream(directory.path() / "broken.outbox", std::ios::binary) << "broken";
  ResultOutbox outbox(directory.path());
  require(outbox.pending().empty(), "corrupt entry was returned");
  require(fs::exists(directory.path() / "broken.corrupt"),
          "corrupt entry was not quarantined");
}

void test_pending_entries_are_bounded() {
  TemporaryDirectory directory;
  ResultOutbox outbox(directory.path(), 2);
  outbox.enqueue(make_result("task-1", true, "", "1"));
  outbox.enqueue(make_result("task-2", true, "", "2"));
  outbox.enqueue(make_result("task-3", true, "", "3"));
  require(outbox.pending().size() == 2, "outbox limit was not enforced");
  std::size_t overflow = 0;
  for (const auto& item : fs::directory_iterator(directory.path())) {
    if (item.path().extension() == ".overflow") ++overflow;
  }
  require(overflow == 1, "overflow entry was not quarantined");
}

void test_successful_replay_acknowledges_entries() {
  TemporaryDirectory directory;
  ResultOutbox outbox(directory.path());
  outbox.enqueue(make_result("task-1", true, "", "1"));
  outbox.enqueue(make_result("task-2", false, "failed", ""));
  std::vector<std::string> delivered;
  const auto count = outbox.replay([&](const TaskResult& result) {
    delivered.push_back(result.task_id);
    return true;
  });
  require(count == 2, "successful replay count mismatch");
  require(delivered.size() == 2, "successful replay delivery count mismatch");
  require(outbox.pending().empty(), "successful replay retained entries");
}

void test_failed_replay_retains_current_and_later_entries() {
  TemporaryDirectory directory;
  ResultOutbox outbox(directory.path());
  outbox.enqueue(make_result("task-1", true, "", "1"));
  outbox.enqueue(make_result("task-2", true, "", "2"));
  std::size_t attempts = 0;
  const auto count = outbox.replay([&](const TaskResult&) {
    ++attempts;
    return false;
  });
  require(count == 0, "failed replay reported a delivery");
  require(attempts == 1, "failed replay did not stop immediately");
  require(outbox.pending().size() == 2, "failed replay removed pending entries");
}

void test_replay_acknowledges_only_delivered_prefix() {
  TemporaryDirectory directory;
  ResultOutbox outbox(directory.path());
  outbox.enqueue(make_result("task-1", true, "", "1"));
  outbox.enqueue(make_result("task-2", true, "", "2"));
  std::size_t attempts = 0;
  const auto count = outbox.replay([&](const TaskResult&) {
    ++attempts;
    return attempts == 1;
  });
  require(count == 1, "partial replay count mismatch");
  require(attempts == 2, "partial replay attempt count mismatch");
  require(outbox.pending().size() == 1, "partial replay pending count mismatch");
}

void test_temporary_entry_is_ignored_during_recovery() {
  TemporaryDirectory directory;
  std::ofstream(directory.path() / "interrupted.tmp", std::ios::binary)
      << "partial";
  ResultOutbox outbox(directory.path());
  require(outbox.pending().empty(), "temporary entry was recovered");
  require(fs::exists(directory.path() / "interrupted.tmp"),
          "temporary entry was unexpectedly modified");
}

}

int main() {
  try {
    test_roundtrip_and_acknowledge();
    test_same_task_replaces_entry();
    test_restart_replays_unacknowledged_entry();
    test_corrupt_entry_is_quarantined();
    test_pending_entries_are_bounded();
    test_successful_replay_acknowledges_entries();
    test_failed_replay_retains_current_and_later_entries();
    test_replay_acknowledges_only_delivered_prefix();
    test_temporary_entry_is_ignored_during_recovery();
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  return 0;
}
