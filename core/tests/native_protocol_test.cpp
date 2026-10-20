#include "drop/native_profiler.h"
#include <algorithm>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

namespace fs = std::filesystem;
std::string exchange(const std::string &path, const std::string &request) {
  const auto descriptor = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (descriptor < 0)
    throw std::runtime_error("Cannot open native protocol fixture");
  struct Guard {
    int value;
    ~Guard() { close(value); }
  } guard{descriptor};
  timeval timeout{1, 0};
  setsockopt(descriptor, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  if (path.size() >= sizeof(address.sun_path))
    throw std::runtime_error("Fixture path exceeds Unix limit");
  std::copy(path.begin(), path.end(), address.sun_path);
  if (connect(descriptor, reinterpret_cast<sockaddr *>(&address),
              sizeof(address)) ||
      send(descriptor, request.data(), request.size(), MSG_NOSIGNAL) !=
          static_cast<ssize_t>(request.size()))
    throw std::runtime_error("Native protocol fixture connection failed");
  char bytes[256]{};
  const auto count = recv(descriptor, bytes, sizeof(bytes), 0);
  if (count <= 0)
    throw std::runtime_error(
        "Native protocol fixture did not reply within its deadline");
  return std::string(bytes, static_cast<std::size_t>(count));
}
int main() {
  const auto root = fs::temp_directory_path() /
                    ("drop-native-protocol-" + std::to_string(getpid()));
  fs::create_directory(root);
  chmod(root.c_str(), 0700);
  if (drop_native_profiler_start(root.c_str(), getuid()))
    throw std::runtime_error("Owned native adapter root was rejected");
  try {
    const auto socket = root / (std::to_string(getpid()) + ".sock");
    for (const auto *request :
         {"{\"schema_version\":1,\"mode\":\"cpu\",\"operation\":\"start\"}\n",
          "{\"schema_version\":2,\"mode\":\"cpu\",\"operation\":\"start\","
          "\"scratch_filesystem_max_bytes\":0,\"path\":\"/tmp/arbitrary\"}\n",
          "{\"schema_version\":2,\"mode\":\"cpu\",\"operation\":\"start\","
          "\"scratch_filesystem_max_bytes\":-1}\n",
          "{\"schema_version\":2,\"mode\":\"cpu\",\"operation\":\"start\","
          "\"scratch_filesystem_max_bytes\":\"2147483648\"}\n",
          "{\"schema_version\":2,\"mode\":\"cpu\",\"operation\":\"start\","
          "\"scratch_filesystem_max_bytes\":1}\n"}) {
      if (!exchange(socket.string(), request).starts_with("ERR "))
        throw std::runtime_error("Unsafe native protocol request accepted");
      if (std::distance(fs::directory_iterator(root),
                        fs::directory_iterator{}) != 1)
        throw std::runtime_error(
            "Rejected native request created profiler scratch");
    }
    if (fs::space(root).capacity > 128ULL * 1024 * 1024) {
      if (!exchange(socket.string(),
                    "{\"schema_version\":2,\"mode\":\"cpu\",\"operation\":"
                    "\"start\",\"scratch_filesystem_max_bytes\":134217728}\n")
               .starts_with("ERR ") ||
          std::distance(fs::directory_iterator(root),
                        fs::directory_iterator{}) != 1)
        throw std::runtime_error("Native scratch filesystem capacity was not "
                                 "rejected before profiler start");
    }
  } catch (...) {
    drop_native_profiler_stop();
    fs::remove_all(root);
    throw;
  }
  drop_native_profiler_stop();
  if (!fs::is_empty(root))
    throw std::runtime_error(
        "Native adapter left scratch after rejected requests");
  fs::remove(root);
  std::cout
      << "native schema2: legacy/path/invalid budgets rejected before scratch; "
         "oversized filesystem fail-closed; owned control-root lifecycle\n";
}
