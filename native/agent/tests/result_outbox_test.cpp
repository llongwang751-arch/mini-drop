#include "result_outbox.h"

#include <chrono>
#include <cstdint>
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
                       const std::string& artifact_json,
                       const std::string& task_attempt_authority = "",
                       const std::string& task_attempt_id = "",
                       const std::string& error_code = "") {
  TaskResult result;
  result.task_id = task_id;
  result.ok = ok;
  result.error = error;
  result.artifact_json = artifact_json;
  result.task_attempt_authority = task_attempt_authority;
  result.task_attempt_id = task_attempt_id;
  result.error_code = error_code;
  return result;
}

void test_v4_roundtrip_and_acknowledge() {
  TemporaryDirectory directory;
  ResultOutbox outbox(directory.path());
  const auto saved = outbox.enqueue(
      make_result("task-1", true, "", "{\"artifact\":\"raw\"}",
                  "authority-1", "attempt-1", "NONE"));
  std::ifstream raw(saved.path, std::ios::binary);
  std::string header(8, '\0');
  raw.read(header.data(), static_cast<std::streamsize>(header.size()));
  require(header == std::string("MDRES04\0", 8), "v4 header mismatch");
  const auto entries = outbox.pending();
  require(entries.size() == 1, "roundtrip entry count");
  require(entries[0].result.task_id == "task-1", "roundtrip task id");
  require(entries[0].result.task_attempt_authority == "authority-1",
          "roundtrip authority");
  require(entries[0].result.task_attempt_id == "attempt-1",
          "roundtrip attempt id");
  require(entries[0].result.error_code == "NONE", "roundtrip error code");
  require(entries[0].result.ok, "roundtrip status");
  require(entries[0].result.artifact_json == "{\"artifact\":\"raw\"}",
          "roundtrip artifact");
  outbox.acknowledge(entries[0]);
  require(outbox.pending().empty(), "acknowledged entry remains pending");
  require(!fs::exists(saved.path), "acknowledged file still exists");
}

void test_same_attempt_replaces_entry() {
  TemporaryDirectory directory;
  ResultOutbox outbox(directory.path());
  outbox.enqueue(make_result("task-1", false, "first", "", "authority-1"));
  outbox.enqueue(make_result("task-1", true, "", "second", "authority-1"));
  const auto entries = outbox.pending();
  require(entries.size() == 1, "same attempt created duplicate entries");
  require(entries[0].result.ok, "replacement status mismatch");
  require(entries[0].result.artifact_json == "second", "replacement payload mismatch");
}

void test_distinct_attempts_have_distinct_entries() {
  TemporaryDirectory directory;
  ResultOutbox outbox(directory.path());
  const auto first = outbox.enqueue(
      make_result("task-1", true, "", "first", "authority-1"));
  const auto second = outbox.enqueue(
      make_result("task-1", true, "", "second", "authority-2"));
  require(first.path != second.path, "distinct attempts shared a path");
  const auto entries = outbox.pending();
  require(entries.size() == 2, "distinct attempts replaced one another");
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

void append_u64(std::string& output, std::uint64_t value) {
  for (int shift = 56; shift >= 0; shift -= 8) {
    output.push_back(static_cast<char>((value >> shift) & 0xff));
  }
}

void append_string(std::string& output, const std::string& value) {
  append_u64(output, value.size());
  output.append(value);
}

void test_legacy_v1_replays_without_authority() {
  TemporaryDirectory directory;
  std::string payload("MDRES01\0", 8);
  payload.push_back('\1');
  append_string(payload, "task-legacy");
  append_string(payload, "");
  append_string(payload, "legacy-artifact");
  std::ofstream output(directory.path() / "legacy.outbox", std::ios::binary);
  output.write(payload.data(), static_cast<std::streamsize>(payload.size()));
  output.close();

  ResultOutbox outbox(directory.path());
  const auto entries = outbox.pending();
  require(entries.size() == 1, "legacy entry was not replayed");
  require(entries[0].result.task_id == "task-legacy", "legacy task mismatch");
  require(entries[0].result.task_attempt_authority.empty(),
          "legacy authority was fabricated");
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
    test_v4_roundtrip_and_acknowledge();
    test_same_attempt_replaces_entry();
    test_distinct_attempts_have_distinct_entries();
    test_restart_replays_unacknowledged_entry();
    test_legacy_v1_replays_without_authority();
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
