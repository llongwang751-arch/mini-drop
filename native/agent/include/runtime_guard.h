#pragma once
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>

namespace mini_drop_native {
// asprof/jattach may send SIGQUIT while starting the JVM attach listener.
// A process name is not proof that this is a JVM. Never probe an unknown
// process with an attach operation: ordinary Go programs can exit on SIGQUIT.
inline bool has_hotspot_runtime(int pid, const std::filesystem::path& proc = "/proc") {
  if (pid <= 0) return false;
  std::ifstream input(proc / std::to_string(pid) / "maps");
  std::string line;
  std::size_t bytes = 0;
  while (std::getline(input, line)) {
    bytes += line.size();
    if (bytes > 4 * 1024 * 1024) return false;
    std::istringstream fields(line);
    std::string address, permissions, offset, device, inode, path;
    if (!(fields >> address >> permissions >> offset >> device >> inode)) continue;
    std::getline(fields >> std::ws, path);
    if (permissions.find('x') != std::string::npos &&
        std::filesystem::path(path).filename() == "libjvm.so") return true;
  }
  return false;
}
}  // namespace mini_drop_native
