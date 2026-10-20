#include "drop/artifact.hpp"
#include "drop/cooperative.hpp"
#include "drop/json.hpp"
#include "drop/process.hpp"
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
using namespace dropcore;
namespace fs = std::filesystem;
template <class F> void rejects(F action) {
  try {
    action();
  } catch (const std::exception &) {
    return;
  }
  throw std::runtime_error("Unsafe cooperative RAW operation accepted");
}
void require(bool value, const char *reason) {
  if (!value)
    throw std::runtime_error(reason);
}
int main(int argc, char **argv) {
  if (argc != 2)
    return 2;
  Catalog catalog(argv[1]);
  const auto root = fs::temp_directory_path() /
                    ("drop-cooperative-test-" + std::to_string(getpid()));
  fs::create_directory(root);
  chmod(root.c_str(), 0700);
  const auto receive = root / "receive", journals = root / "journals",
             journal = journals / "attempt-7";
  fs::create_directory(receive);
  chmod(receive.c_str(), 01770);
  fs::create_directory(journals);
  fs::create_directory(journal);
  CooperativeFiles store(catalog, receive, "fixture-agent", 2147483648ULL,
                         false);
  require(!store.bounded_filesystem(),
          "Isolated software fixture was mislabeled as a physical quota proof");
  store.prepare_native_control(receive / "native-control");
  struct stat control {};
  require(lstat((receive / "native-control").c_str(), &control) == 0 &&
              control.st_uid == geteuid() && (control.st_mode & 0777) == 0700,
          "Native scratch control root was not safely prepared as private");
  store.prepare_native_control(receive / "native-control");
  rejects([&] { store.prepare_native_control(root / "not-prepared-outside"); });
  fs::create_symlink(receive / "native-control", receive / "control-alias");
  rejects([&] { store.prepare_native_control(receive / "control-alias"); });
  const auto output = store.prepare(journal, "JAVA_CPU", 7);
  struct stat status {};
  require(lstat(output.c_str(), &status) == 0 && status.st_uid == geteuid() &&
              (status.st_mode & 0777) == 0700,
          "Cooperative RAW directory is not private to Agent UID");
  const auto marker =
      parse_json(read_file((journal / "cooperative-output.json").string()));
  require(marker.size() == 5 && marker.at("kind") == "JAVA_CPU" &&
              marker.dump().find("/tmp/") == std::string::npos,
          "Cooperative recovery journal exposed a path or credential");
  rejects([&] { store.prepare(journal, "JAVA_CPU", 7); });
  rejects([&] { store.raw(journal, "../../outside"); });
  rejects([&] { store.raw(journal, "heap.hprof"); });
  const auto raw = store.raw(journal, "java.folded");
  atomic_write(raw.string(), "imported_java_fixture;frame 5\n");
  store.sync(journal, "java.folded", 1024);
  require(store.bytes(4096) == fs::file_size(raw),
          "Cooperative bytes did not enter retention admission");
  rejects([&] { store.sync(journal, "java.folded", 1); });
  const auto original_sha = sha256_file(raw.string());
  CooperativeFiles restarted(catalog, receive, "fixture-agent", 2147483648ULL,
                             false);
  require(restarted.raw(journal, "java.folded") == raw &&
              sha256_file(restarted.raw(journal, "java.folded").string()) ==
                  original_sha,
          "Restart did not resolve the exact persisted RAW");
  fs::rename(raw, raw.string() + ".saved");
  if (mkfifo(raw.c_str(), 0600))
    throw std::runtime_error("Cannot create FIFO RAW fixture");
  rejects([&] { store.sync(journal, "java.folded", 1024); });
  fs::remove(raw);
  fs::create_symlink(raw.string() + ".saved", raw);
  rejects([&] { store.sync(journal, "java.folded", 1024); });
  fs::remove(raw);
  fs::rename(raw.string() + ".saved", raw);
  fs::create_hard_link(raw, output / "linked");
  rejects([&] { store.sync(journal, "java.folded", 1024); });
  fs::remove(output / "linked");
  const auto aged = fs::file_time_type::clock::now() - std::chrono::hours(2);
  fs::last_write_time(raw, aged);
  fs::last_write_time(output, aged);
  require(store.sweep(journals, 300) == 0 && fs::exists(raw),
          "Retention deleted a referenced RAW");
  fs::remove(journal / "cooperative-output.json");
  require(store.sweep(journals, 300) == 1 && !fs::exists(output),
          "Expired orphan RAW was not cleaned within its owner namespace");
  const auto heap_output = store.prepare(journal, "JAVA_HEAP", 7);
  atomic_write((heap_output / "heap.hprof").string(),
               "IMPORTED_HPROF_BYTES_NOT_REAL_DUMP");
  store.sync(journal, "heap.hprof", 1024);
  fs::remove_all(
      heap_output); // 模拟删除RAW之后、删除标记之前崩溃，检查恢复清理是否仍正确。
  store.remove(journal);
  store.remove(journal);
  require(!fs::exists(heap_output) &&
              !fs::exists(journal / "cooperative-output.json"),
          "Completed cooperative RAW and marker were not cleaned");
  const auto other = root / "outside";
  fs::create_directory(other);
  fs::create_symlink(other,
                     receive / ("agent-fixture-agent-" + std::string(64, 'a')));
  require(store.bytes(4096) == 4096,
          "Unsafe receive entry did not fail closed against its budget");
  fs::remove_all(root);
  std::cout << "cooperative RAW: private fixed filenames, fsync, cold replay, "
               "bounded accounting, FIFO/symlink/hardlink rejection, "
               "referenced TTL protection and orphan cleanup\n";
}
