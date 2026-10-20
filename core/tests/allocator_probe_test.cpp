// Filesystem safety fixtures only. This test does not claim kernel collection.
#include "drop/allocator_probe.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

namespace fs = std::filesystem;
template <class F> void rejects(F &&f) {
  try {
    f();
  } catch (const std::exception &) {
    return;
  }
  throw std::runtime_error("Unsafe allocator alias accepted");
}
int main() {
  using namespace dropcore;
  const auto target = process_identity(getpid());
  if (geteuid() != 0) {
    rejects([&] { AllocatorProbeAlias alias(target, "/proc/self/root/tmp/a"); });
    std::cout << "non-root allocator alias refused\n";
    return 0;
  }
  const auto directory = fs::temp_directory_path() /
                         ("drop-alias-fixture-" + std::to_string(getpid()));
  if (!fs::create_directory(directory))
    throw std::runtime_error("Alias safety fixture already exists");
  const auto file = directory / "library-fixture";
  std::ofstream(file) << "Imported safety fixture, not a native library\n";
  const auto library = "/proc/" + std::to_string(getpid()) + "/root" +
                       file.string();
  std::string path;
  {
    AllocatorProbeAlias alias(target, library);
    path = alias.target_path();
    if (!path.starts_with("/tmp/u.") || path.size() > 24)
      throw std::runtime_error("Allocator alias lost its bounded short name");
    struct stat link {}, source {}, parent {};
    if (stat(path.c_str(), &link) || stat(file.c_str(), &source) ||
        stat(fs::path(path).parent_path().c_str(), &parent) ||
        link.st_dev != source.st_dev || link.st_ino != source.st_ino ||
        parent.st_uid != 0 || (parent.st_mode & 0777) != 0700)
      throw std::runtime_error("Allocator alias copied or exposed the library");
    alias.verify();
  }
  if (fs::exists(fs::path(path).parent_path()))
    throw std::runtime_error("Allocator alias remained after completion");
  auto stale = target;
  ++stale.start_time_ticks;
  rejects([&] { AllocatorProbeAlias alias(stale, library); });
  rejects([&] { AllocatorProbeAlias alias(target, file.string()); });
  rejects([&] { AllocatorProbeAlias alias(target, library + "\n"); });
  rejects([&] { AllocatorProbeAlias alias(target, library + "/../x"); });
  rejects([&] {
    AllocatorProbeAlias alias(target, "/proc/" + std::to_string(getpid()) +
                                          "/root/dev/null");
  });
  const auto descriptors = [] {
    return std::distance(fs::directory_iterator("/proc/self/fd"),
                         fs::directory_iterator{});
  };
  const auto before = descriptors();
  for (unsigned attempt = 0; attempt < 32; ++attempt)
    rejects([&] {
      AllocatorProbeAlias alias(target, "/proc/" + std::to_string(getpid()) +
                                            "/root/dev/null");
    });
  if (descriptors() != before)
    throw std::runtime_error("Failed allocator aliases leaked descriptors");
  {
    AllocatorProbeAlias alias(target, library);
    path = alias.target_path();
    const auto replacement = directory / "replacement";
    std::ofstream(replacement) << "Changed source inode fixture\n";
    fs::rename(replacement, file);
    rejects([&] { alias.verify(); });
  }
  if (fs::exists(fs::path(path).parent_path()))
    throw std::runtime_error("Failed collection did not clean its alias");
  {
    AllocatorProbeAlias alias(target, library);
    path = alias.target_path();
    fs::remove(path);
    std::ofstream(path) << "Unrelated replacement fixture, never remove\n";
    rejects([&] { alias.verify(); });
  }
  if (!fs::is_regular_file(path))
    throw std::runtime_error("Alias cleanup removed an unrelated replacement");
  fs::remove(path);
  fs::remove(fs::path(path).parent_path());
  fs::remove(file);
  fs::remove(directory);
  std::cout << "allocator alias identity, ownership, cleanup and replacement "
               "checks passed\n";
}
