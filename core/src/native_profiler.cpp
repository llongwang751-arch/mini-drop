#include "drop/native_profiler.h"
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <functional>
#include <memory>
#include <mutex>
#include <nlohmann/json.hpp>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {
using json = nlohmann::json;
struct ScopeCleanup {
  std::function<void()> action;
  ~ScopeCleanup() { action(); }
};
template <class T> T symbol(const char *name) {
  auto *value = dlsym(RTLD_DEFAULT, name);
  if (!value)
    throw std::runtime_error(
        "Application was not linked with the requested profiler");
  return reinterpret_cast<T>(value);
}
class Adapter {
public:
  ~Adapter() { stop(); }
  void start(const std::string &root, uint32_t controller) {
    struct stat info {};
    if (!std::filesystem::path(root).is_absolute() ||
        std::filesystem::path(root) ==
            std::filesystem::path(root).root_path() ||
        lstat(root.c_str(), &info) != 0 || !S_ISDIR(info.st_mode) ||
        info.st_uid != getuid() || (info.st_mode & 0077))
      throw std::runtime_error(
          "Control directory requires application ownership and mode0700");
    socket_path_ = root + "/" + std::to_string(getpid()) + ".sock";
    root_ = std::filesystem::canonical(root).string();
    if (socket_path_.size() >= sizeof(sockaddr_un::sun_path))
      throw std::runtime_error("Socket path too long");
    fd_ = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd_ < 0)
      throw std::runtime_error("Socket unavailable");
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::copy(socket_path_.begin(), socket_path_.end(), address.sun_path);
    if (bind(fd_, reinterpret_cast<sockaddr *>(&address), sizeof(address)) !=
            0 ||
        chmod(socket_path_.c_str(), 0600) != 0 || listen(fd_, 4) != 0) {
      close(fd_);
      fd_ = -1;
      throw std::runtime_error("Control listener unavailable");
    }
    controller_ = controller;
    thread_ = std::jthread([this](std::stop_token token) {
      while (!token.stop_requested()) {
        pollfd poller{fd_, POLLIN, 0};
        if (poll(&poller, 1, 250) <= 0) {
          if (!mode_.empty() && std::chrono::steady_clock::now() > deadline_) {
            try {
              finish();
            } catch (...) {
            }
          }
          continue;
        }
        const auto peer = accept4(fd_, nullptr, nullptr, SOCK_CLOEXEC);
        if (peer < 0)
          continue;
        timeval timeout{5, 0};
        setsockopt(peer, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        setsockopt(peer, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        try {
          ucred identity{};
          socklen_t length = sizeof(identity);
          if (getsockopt(peer, SOL_SOCKET, SO_PEERCRED, &identity, &length) !=
                  0 ||
              (identity.uid != getuid() && identity.uid != controller_))
            throw std::runtime_error("Controller UID not permitted");
          std::string input;
          char ch = 0;
          while (input.size() < 1024 && recv(peer, &ch, 1, 0) == 1 &&
                 ch != '\n')
            input += ch;
          if (input.size() >= 1024)
            throw std::runtime_error("Request exceeds budget");
          const auto request = json::parse(input);
          const auto mode = request.at("mode").get<std::string>(),
                     operation = request.at("operation").get<std::string>();
          if (request.at("schema_version") != 2 || request.size() != 4 ||
              (mode != "cpu" && mode != "heap" && mode != "jemalloc"))
            throw std::runtime_error("Unsupported control command");
          const auto &maximum_value =
              request.at("scratch_filesystem_max_bytes");
          if (!maximum_value.is_number_unsigned() &&
              !maximum_value.is_number_integer())
            throw std::runtime_error("Invalid native scratch bound");
          const auto maximum = maximum_value.get<std::int64_t>();
          if (maximum < 0 ||
              (maximum && (maximum < 128LL * 1024 * 1024 ||
                           maximum > 8LL * 1024 * 1024 * 1024 ||
                           std::filesystem::space(root_).capacity >
                               static_cast<std::uint64_t>(maximum))))
            throw std::runtime_error(
                "Native scratch is not on the requested bounded filesystem");
          std::string output;
          if (operation == "start")
            begin(mode);
          else if (operation == "stop") {
            if (mode != mode_)
              throw std::runtime_error("Profiler session mismatch");
            output = finish();
          } else
            throw std::runtime_error("Unsupported operation");
          send_all(peer, "OK2 " + std::to_string(output.size()) + "\n");
          send_all(peer, output);
        } catch (const std::exception &) {
          try {
            send_all(peer, "ERR profiler unavailable or invalid session\n");
          } catch (...) {
          }
        }
        close(peer);
      }
    });
  }
  void stop() {
    if (thread_.joinable()) {
      thread_.request_stop();
      thread_.join();
    }
    if (fd_ >= 0) {
      close(fd_);
      fd_ = -1;
      unlink(socket_path_.c_str());
    }
    if (!mode_.empty()) {
      try {
        finish();
      } catch (...) {
      }
    }
  }

private:
  static void send_all(int socket, const std::string &text) {
    std::size_t sent = 0;
    while (sent < text.size()) {
      const auto amount =
          send(socket, text.data() + sent, text.size() - sent, MSG_NOSIGNAL);
      if (amount <= 0)
        throw std::runtime_error("Controller disconnected");
      sent += static_cast<std::size_t>(amount);
    }
  }
  void begin(const std::string &mode) {
    if (!mode_.empty())
      throw std::runtime_error("An active profiling session already exists");
    // The embedding application preconfigures this owned0700 control root.
    // Both profiler scratch and socket stay on its filesystem; no peer supplies
    // a path and a schema2 controller can require a real filesystem quota.
    std::vector<char> temporary;
    const auto name =
        root_ + "/drop-native-" + std::to_string(getpid()) + "-XXXXXX";
    temporary.resize(name.size() + 1);
    std::copy(name.begin(), name.end(), temporary.begin());
    auto file = mkstemp(temporary.data());
    if (file < 0)
      throw std::runtime_error("Private profile file unavailable");
    close(file);
    output_ = temporary.data();
    try {
      if (mode == "cpu") {
        if (!symbol<int (*)(const char *)>("ProfilerStart")(output_.c_str()))
          throw std::runtime_error("CPU profiler refused start");
      } else if (mode == "heap")
        symbol<void (*)(const char *)>("HeapProfilerStart")(output_.c_str());
      else {
        bool enabled = true;
        if (symbol<int (*)(const char *, void *, size_t *, void *, size_t)>(
                "mallctl")("prof.active", nullptr, nullptr, &enabled,
                           sizeof(enabled)))
          throw std::runtime_error(
              "jemalloc must be built/configured with prof:true");
      }
      mode_ = mode;
      deadline_ = std::chrono::steady_clock::now() + std::chrono::seconds(90);
    } catch (...) {
      unlink(output_.c_str());
      output_.clear();
      throw;
    }
  }
  std::string finish() {
    const auto mode = std::exchange(mode_, "");
    ScopeCleanup cleanup{[this] { cleanup_output(); }};
    std::string result;
    if (mode == "cpu")
      symbol<void (*)()>("ProfilerStop")();
    else if (mode == "heap") {
      symbol<void (*)(const char *)>("HeapProfilerDump")(
          "drop controlled snapshot");
      auto *profile = symbol<char *(*)()>("GetHeapProfile")();
      symbol<void (*)()>("HeapProfilerStop")();
      if (profile) {
        std::unique_ptr<char, decltype(&std::free)> allocation(profile,
                                                               &std::free);
        const auto size = strnlen(profile, 256ULL * 1024 * 1024 + 1);
        if (size > 256ULL * 1024 * 1024)
          throw std::runtime_error("Native profile exceeds transfer budget");
        result.assign(profile, size);
      }
    } else if (mode == "jemalloc") {
      const char *path = output_.c_str();
      auto ctl =
          symbol<int (*)(const char *, void *, size_t *, void *, size_t)>(
              "mallctl");
      const auto status =
          ctl("prof.dump", nullptr, nullptr, &path, sizeof(path));
      bool active = false;
      ctl("prof.active", nullptr, nullptr, &active, sizeof(active));
      if (status)
        throw std::runtime_error("jemalloc profile dump failed");
    }
    if (mode != "heap") {
      const auto descriptor =
          open(output_.c_str(), O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC);
      struct Close {
        int value;
        ~Close() {
          if (value >= 0)
            close(value);
        }
      } input{descriptor};
      struct stat status {};
      if (descriptor < 0 || fstat(descriptor, &status) ||
          !S_ISREG(status.st_mode) || status.st_uid != getuid() ||
          status.st_nlink != 1 || status.st_size <= 0 ||
          status.st_size > 256LL * 1024 * 1024)
        throw std::runtime_error(
            "Native profile is not a bounded owned regular file");
      std::array<char, 65536> bytes{};
      for (;;) {
        const auto count = read(descriptor, bytes.data(), bytes.size());
        if (count < 0 && errno == EINTR)
          continue;
        if (count < 0 ||
            (count > 0 && result.size() + static_cast<std::size_t>(count) >
                              256ULL * 1024 * 1024))
          throw std::runtime_error("Native profile read exceeded its budget");
        if (!count)
          break;
        result.append(bytes.data(), static_cast<std::size_t>(count));
      }
    }
    if (result.empty())
      throw std::runtime_error("Profiler produced no data");
    return result;
  }
  void cleanup_output() noexcept {
    if (output_.empty())
      return;
    std::error_code ignored;
    std::filesystem::remove(output_, ignored);
    unsigned files = 0;
    for (std::filesystem::directory_iterator iterator(root_, ignored), end;
         !ignored && iterator != end && files++ < 10000;
         iterator.increment(ignored)) {
      const auto name = iterator->path().string();
      if (name.starts_with(output_ + ".") && name.ends_with(".heap"))
        std::filesystem::remove(iterator->path(), ignored);
    }
    output_.clear();
  }
  int fd_ = -1;
  uint32_t controller_ = 0;
  std::string socket_path_, root_, mode_, output_;
  std::jthread thread_;
  std::chrono::steady_clock::time_point deadline_;
};
std::mutex mutex;
std::unique_ptr<Adapter> adapter;
} // namespace
extern "C" int drop_native_profiler_start(const char *directory,
                                          uint32_t controller_uid) {
  try {
    std::lock_guard lock(mutex);
    if (adapter || !directory)
      return -1;
    auto candidate = std::make_unique<Adapter>();
    candidate->start(directory, controller_uid);
    adapter = std::move(candidate);
    return 0;
  } catch (...) {
    return -1;
  }
}
extern "C" void drop_native_profiler_stop() {
  std::lock_guard lock(mutex);
  adapter.reset();
}
