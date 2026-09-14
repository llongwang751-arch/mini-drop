#include "runtime_guard.h"
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <unistd.h>

int main() {
  namespace fs = std::filesystem;
  const auto root = fs::temp_directory_path() / ("runtime-guard-" + std::to_string(getpid()));
  fs::create_directories(root / "42");
  const auto maps = root / "42/maps";
  auto check = [&](const std::string& contents, bool expected) {
    std::ofstream(maps) << contents;
    if (mini_drop_native::has_hotspot_runtime(42, root) != expected)
      throw std::runtime_error("runtime guard mismatch");
  };
  check("", false);
  check("1000-2000 r-xp 0 00:00 1 /opt/java-named-directory/filebrowser\n", false);
  check("1000-2000 r-xp 0 00:00 1 /opt/libjvm.so.fake\n", false);
  check("1000-2000 r--p 0 00:00 1 /opt/libjvm.so\n", false);
  check("1000-2000 r-xp 0 00:00 1 /usr/lib/jvm/lib/server/libjvm.so\n", true);
  if (mini_drop_native::has_hotspot_runtime(getpid()))
    throw std::runtime_error("ordinary native process mistaken for a JVM");
  fs::remove_all(root);
}
