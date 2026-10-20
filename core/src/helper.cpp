#include "drop/helper.hpp"
#include "drop/artifact.hpp"
#include "drop/domain.hpp"
#include "drop/json.hpp"
#include "drop/sampling.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <future>
#include <map>
#include <mutex>
#include <optional>
#include <poll.h>
#include <regex>
#include <set>
#include <sstream>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

namespace fs = std::filesystem;
using json = nlohmann::json;
using namespace std::chrono_literals;

namespace dropcore {
namespace {
constexpr std::size_t header_limit = 65536;
constexpr std::uint64_t absolute_file_limit = 512ULL * 1024 * 1024;
std::atomic<bool> helper_alive{true};
void helper_stop(int) { helper_alive = false; }
std::int64_t now_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}
struct Descriptor {
  int value = -1;
  explicit Descriptor(int fd) : value(fd) {}
  ~Descriptor() {
    if (value >= 0)
      close(value);
  }
  Descriptor(const Descriptor &) = delete;
  Descriptor &operator=(const Descriptor &) = delete;
};
struct ScopeExit {
  std::function<void()> action;
  ~ScopeExit() {
    try {
      action();
    } catch (...) {
    }
  }
};
void keys(const json &value, std::initializer_list<const char *> allowed) {
  if (!value.is_object())
    throw HelperFailure("INVALID_HELPER_REQUEST");
  for (const auto &entry : value.items())
    if (std::find(allowed.begin(), allowed.end(), entry.key()) == allowed.end())
      throw HelperFailure("INVALID_HELPER_REQUEST");
}
std::uint64_t positive(const json &value, const char *key,
                       std::uint64_t maximum, bool zero = false) {
  if (!value.contains(key) || !value.at(key).is_number_integer() ||
      value.at(key) < 0)
    throw HelperFailure("INVALID_HELPER_REQUEST");
  const auto result = value.at(key).get<std::uint64_t>();
  if ((!zero && result == 0) || result > maximum)
    throw HelperFailure("INVALID_HELPER_REQUEST");
  return result;
}
sockaddr_un address(const std::string &path) {
  sockaddr_un result{};
  result.sun_family = AF_UNIX;
  if (path.empty() || path.front() != '/' ||
      path.size() >= sizeof(result.sun_path) ||
      path.find('\0') != std::string::npos)
    throw HelperFailure("INVALID_HELPER_CONFIGURATION");
  std::copy(path.begin(), path.end(), result.sun_path);
  return result;
}
void wait_socket(int fd, short events, const std::function<bool()> &canceled,
                 std::chrono::steady_clock::time_point deadline) {
  for (;;) {
    if (canceled())
      throw HelperFailure("TASK_CANCELED");
    if (std::chrono::steady_clock::now() >= deadline)
      throw HelperFailure("HELPER_TIMEOUT");
    pollfd item{fd, events, 0};
    const auto ready = poll(&item, 1, 100);
    if (ready < 0 && errno != EINTR)
      throw HelperFailure("HELPER_UNAVAILABLE");
    if (ready > 0) {
      if (item.revents & events)
        return;
      if (item.revents & (POLLERR | POLLHUP | POLLNVAL))
        throw HelperFailure("HELPER_UNAVAILABLE");
    }
  }
}
void read_bytes(int fd, char *buffer, std::size_t bytes,
                const std::function<bool()> &canceled,
                std::chrono::steady_clock::time_point deadline) {
  while (bytes) {
    wait_socket(fd, POLLIN, canceled, deadline);
    const auto count = recv(fd, buffer, bytes, MSG_DONTWAIT);
    if (count < 0 && (errno == EINTR || errno == EAGAIN))
      continue;
    if (count <= 0)
      throw HelperFailure("HELPER_UNAVAILABLE");
    buffer += count;
    bytes -= static_cast<std::size_t>(count);
  }
}
void send_bytes(int fd, const char *buffer, std::size_t bytes,
                const std::function<bool()> &canceled,
                std::chrono::steady_clock::time_point deadline) {
  while (bytes) {
    wait_socket(fd, POLLOUT, canceled, deadline);
    const auto count = send(fd, buffer, bytes, MSG_NOSIGNAL | MSG_DONTWAIT);
    if (count < 0 && (errno == EINTR || errno == EAGAIN))
      continue;
    if (count <= 0)
      throw HelperFailure("HELPER_UNAVAILABLE");
    buffer += count;
    bytes -= static_cast<std::size_t>(count);
  }
}
void send_header(
    int fd, const json &value,
    const std::function<bool()> &canceled = [] { return false; }) {
  const auto encoded = value.dump();
  if (encoded.empty() || encoded.size() > header_limit)
    throw HelperFailure("HELPER_PROTOCOL_ERROR");
  const auto length = static_cast<std::uint32_t>(encoded.size());
  std::array<char, 4> prefix{
      static_cast<char>(length >> 24), static_cast<char>(length >> 16),
      static_cast<char>(length >> 8), static_cast<char>(length)};
  const auto deadline = std::chrono::steady_clock::now() + 5s;
  send_bytes(fd, prefix.data(), prefix.size(), canceled, deadline);
  send_bytes(fd, encoded.data(), encoded.size(), canceled, deadline);
}
json read_header(int fd, const std::function<bool()> &canceled,
                 std::chrono::steady_clock::time_point deadline) {
  std::array<unsigned char, 4> prefix{};
  read_bytes(fd, reinterpret_cast<char *>(prefix.data()), prefix.size(),
             canceled, deadline);
  const auto length = (std::uint32_t(prefix[0]) << 24) |
                      (std::uint32_t(prefix[1]) << 16) |
                      (std::uint32_t(prefix[2]) << 8) | prefix[3];
  if (!length || length > header_limit)
    throw HelperFailure("HELPER_PROTOCOL_ERROR");
  std::string encoded(length, '\0');
  read_bytes(fd, encoded.data(), encoded.size(), canceled, deadline);
  return parse_json(encoded, header_limit);
}
int connect_helper(const std::string &path) {
  Descriptor socket_fd(
      socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0));
  if (socket_fd.value < 0)
    throw HelperFailure("HELPER_UNAVAILABLE");
  const auto endpoint = address(path);
  if (connect(socket_fd.value, reinterpret_cast<const sockaddr *>(&endpoint),
              sizeof(endpoint)) != 0) {
    if (errno != EINPROGRESS && errno != EAGAIN)
      throw HelperFailure("HELPER_UNAVAILABLE");
    wait_socket(
        socket_fd.value, POLLOUT, [] { return false; },
        std::chrono::steady_clock::now() + 3s);
    int error = 0;
    socklen_t error_length = sizeof(error);
    if (getsockopt(socket_fd.value, SOL_SOCKET, SO_ERROR, &error,
                   &error_length) ||
        error)
      throw HelperFailure("HELPER_UNAVAILABLE");
  }
  ucred peer{};
  socklen_t length = sizeof(peer);
  if (getsockopt(socket_fd.value, SOL_SOCKET, SO_PEERCRED, &peer, &length) !=
          0 ||
      peer.uid != 0 || peer.pid <= 0)
    throw HelperFailure("HELPER_PEER_DENIED");
  const auto result = socket_fd.value;
  socket_fd.value = -1;
  return result;
}
bool disconnected(int fd) {
  char byte{};
  const auto count = recv(fd, &byte, 1, MSG_PEEK | MSG_DONTWAIT);
  return count == 0 || (count < 0 && errno != EAGAIN && errno != EINTR);
}
void check_response(const json &response) {
  if (!response.is_object() || response.value("protocol_version", 0) != 1)
    throw HelperFailure("HELPER_PROTOCOL_ERROR");
  if (!response.value("ok", false)) {
    const auto code = response.value("error_code", "HELPER_PROTOCOL_ERROR");
    static const std::set<std::string> allowed{
        "INVALID_HELPER_REQUEST",   "HELPER_BUSY",
        "HELPER_NOT_READY",         "HELPER_RESTARTED",
        "HELPER_ACKNOWLEDGED",      "HELPER_PEER_DENIED",
        "HELPER_UNAVAILABLE",       "HELPER_PROTOCOL_ERROR",
        "HELPER_TIMEOUT",           "TASK_CANCELED",
        "TARGET_VALIDATION_FAILED", "CAPABILITY_UNAVAILABLE",
        "DISK_BUDGET_EXCEEDED",     "PROFILER_PERMISSION_DENIED",
        "TRACE_ATTACH_UNAVAILABLE", "TOOL_INTEGRITY_CHANGED",
        "TARGET_CHANGED",           "RUNNER_FAILED",
        "BOLT_BINARY_SAMPLES_UNAVAILABLE", "BOLT_PERF_CONVERSION_FAILED",
        "BOLT_AGGREGATION_FAILED", "BOLT_TARGET_MAPPING_UNVERIFIED"};
    throw HelperFailure(allowed.contains(code) ? code
                                               : "HELPER_PROTOCOL_ERROR");
  }
}
void sync_directory(const fs::path &path) {
  Descriptor directory(
      open(path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
  if (directory.value < 0 || fsync(directory.value))
    throw HelperFailure("DISK_BUDGET_EXCEEDED");
}
void save_file(int socket_fd, const fs::path &directory, const json &file,
               const std::function<bool()> &canceled) {
  const auto name = file.at("name").get<std::string>();
  const auto size = file.at("size").get<std::uint64_t>();
  const auto temporary = directory / (".helper-" + name + ".tmp");
  Descriptor output(
      open(temporary.c_str(),
           O_WRONLY | O_CREAT | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW, 0600));
  if (output.value < 0)
    throw HelperFailure("DISK_BUDGET_EXCEEDED");
  struct stat destination {};
  if (fstat(output.value, &destination) || !S_ISREG(destination.st_mode) ||
      destination.st_uid != geteuid() || destination.st_nlink != 1 ||
      ftruncate(output.value, 0))
    throw HelperFailure("DISK_BUDGET_EXCEEDED");
  try {
    std::array<char, 65536> bytes{};
    std::uint64_t remaining = size;
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(15 + size / (1024 * 1024));
    while (remaining) {
      const auto count = static_cast<std::size_t>(
          std::min<std::uint64_t>(remaining, bytes.size()));
      read_bytes(socket_fd, bytes.data(), count, canceled, deadline);
      std::size_t written = 0;
      while (written < count) {
        const auto value =
            write(output.value, bytes.data() + written, count - written);
        if (value < 0 && errno == EINTR)
          continue;
        if (value <= 0)
          throw HelperFailure("DISK_BUDGET_EXCEEDED");
        written += static_cast<std::size_t>(value);
      }
      remaining -= count;
    }
    if (fsync(output.value) ||
        sha256_file(temporary.string()) != file.at("sha256").get<std::string>())
      throw HelperFailure("HELPER_PROTOCOL_ERROR");
    fs::rename(temporary, directory / name);
    sync_directory(directory);
  } catch (...) {
    fs::remove(temporary);
    throw;
  }
}
std::string perf_path() {
  if (!secret("PERF_EXECUTABLE").empty())
    return secret("PERF_EXECUTABLE");
  if (fs::is_directory("/usr/lib/linux-tools"))
    for (const auto &entry : fs::directory_iterator("/usr/lib/linux-tools"))
      if (access((entry.path() / "perf").c_str(), X_OK) == 0)
        return (entry.path() / "perf").string();
  return "/usr/bin/perf";
}
bool descendant(int target, int parent) {
  for (int depth = 0; depth < 32 && target > 1; ++depth) {
    std::istringstream status(
        read_file("/proc/" + std::to_string(target) + "/status", 65536));
    std::string line;
    int next = 0;
    while (std::getline(status, line))
      if (line.starts_with("PPid:"))
        next = std::stoi(line.substr(5));
    if (next == parent)
      return true;
    if (next <= 0 || next == target)
      return false;
    target = next;
  }
  return false;
}
json file_manifest(const fs::path &path, const std::string &name,
                   const std::string &kind) {
  Descriptor input(open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
  struct stat info {};
  if (input.value < 0 || fstat(input.value, &info) || !S_ISREG(info.st_mode) ||
      info.st_uid != 0 || info.st_size <= 0 || fsync(input.value))
    throw HelperFailure("HELPER_PROTOCOL_ERROR");
  return {{"name", name},
          {"kind", kind},
          {"size", info.st_size},
          {"sha256", sha256_file(path.string())}};
}
void copy_target_binary(const ProcessIdentity &identity,
                        const fs::path &destination, std::uint64_t maximum) {
  Descriptor input(
      open(("/proc/" + std::to_string(identity.pid) + "/exe").c_str(),
           O_RDONLY | O_CLOEXEC));
  struct stat source {};
  if (input.value < 0 || fstat(input.value, &source) ||
      !S_ISREG(source.st_mode) || source.st_size <= 0 ||
      static_cast<std::uint64_t>(source.st_size) > maximum ||
      static_cast<std::uint64_t>(source.st_dev) != identity.executable_device ||
      static_cast<std::uint64_t>(source.st_ino) != identity.executable_inode)
    throw HelperFailure("TARGET_VALIDATION_FAILED");
  Descriptor output(open(destination.c_str(),
                         O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
                         0600));
  if (output.value < 0)
    throw HelperFailure("DISK_BUDGET_EXCEEDED");
  std::array<char, 65536> bytes{};
  std::uint64_t copied = 0;
  for (;;) {
    const auto size = read(input.value, bytes.data(), bytes.size());
    if (size < 0 && errno == EINTR)
      continue;
    if (size < 0)
      throw HelperFailure("TARGET_VALIDATION_FAILED");
    if (!size)
      break;
    if (static_cast<std::uint64_t>(size) > maximum - copied)
      throw HelperFailure("DISK_BUDGET_EXCEEDED");
    std::size_t written = 0;
    while (written < static_cast<std::size_t>(size)) {
      const auto count = write(output.value, bytes.data() + written,
                               static_cast<std::size_t>(size) - written);
      if (count < 0 && errno == EINTR)
        continue;
      if (count <= 0)
        throw HelperFailure("DISK_BUDGET_EXCEEDED");
      written += static_cast<std::size_t>(count);
    }
    copied += static_cast<std::uint64_t>(size);
  }
  struct stat after {};
  if (fstat(input.value, &after) || source.st_size != after.st_size ||
      source.st_mtim.tv_sec != after.st_mtim.tv_sec ||
      source.st_mtim.tv_nsec != after.st_mtim.tv_nsec ||
      source.st_ctim.tv_sec != after.st_ctim.tv_sec ||
      source.st_ctim.tv_nsec != after.st_ctim.tv_nsec ||
      copied != static_cast<std::uint64_t>(source.st_size) ||
      fsync(output.value))
    throw HelperFailure("TARGET_VALIDATION_FAILED");
}
bool cooperative_kind(const std::string &kind) {
  return kind == "PYTHON_MEMORY";
}
bool has_read_search_capability() {
  std::istringstream status(read_file("/proc/self/status", 65536));
  std::string line;
  while (std::getline(status, line))
    if (line.starts_with("CapEff:"))
      return (std::stoull(line.substr(7), nullptr, 16) & (1ULL << 2)) != 0;
  return false;
}
bool process_in_group(int pid, gid_t group) {
  std::istringstream input(
      read_file("/proc/" + std::to_string(pid) + "/status", 65536));
  std::string line;
  bool effective = false;
  while (std::getline(input, line)) {
    if (line.starts_with("Gid:")) {
      std::istringstream values(line.substr(4));
      unsigned real{}, selected{}, saved{}, filesystem{};
      if (values >> real >> selected >> saved >> filesystem)
        effective = filesystem == group;
    }
    if (line.starts_with("Groups:")) {
      std::istringstream values(line.substr(7));
      unsigned selected{};
      while (values >> selected)
        if (selected == group)
          effective = true;
    }
  }
  return effective;
}
struct IncomingDirectory {
  fs::path path;
  ~IncomingDirectory() {
    if (!path.empty()) {
      std::error_code ignored;
      fs::remove_all(path, ignored);
    }
  }
};
void freeze_cooperative_output(const fs::path &source,
                               const fs::path &destination, unsigned owner,
                               std::uint64_t maximum) {
  Descriptor input(
      open(source.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW));
  struct stat before {
  }, after{};
  if (input.value < 0 || fstat(input.value, &before) ||
      !S_ISREG(before.st_mode) ||
      (before.st_uid != owner && before.st_uid != 0) || before.st_nlink != 1 ||
      before.st_size <= 0 ||
      static_cast<std::uint64_t>(before.st_size) > maximum)
    throw HelperFailure("TARGET_VALIDATION_FAILED");
  const auto temporary = destination.string() + ".incoming";
  Descriptor output(open(temporary.c_str(),
                         O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
                         0600));
  if (output.value < 0)
    throw HelperFailure("DISK_BUDGET_EXCEEDED");
  std::array<char, 65536> bytes{};
  std::uint64_t copied = 0;
  for (;;) {
    const auto count = read(input.value, bytes.data(), bytes.size());
    if (count < 0 && errno == EINTR)
      continue;
    if (count < 0)
      throw HelperFailure("TARGET_VALIDATION_FAILED");
    if (!count)
      break;
    if (static_cast<std::uint64_t>(count) > maximum - copied)
      throw HelperFailure("DISK_BUDGET_EXCEEDED");
    std::size_t written = 0;
    while (written < static_cast<std::size_t>(count)) {
      const auto size = write(output.value, bytes.data() + written,
                              static_cast<std::size_t>(count) - written);
      if (size < 0 && errno == EINTR)
        continue;
      if (size <= 0)
        throw HelperFailure("DISK_BUDGET_EXCEEDED");
      written += static_cast<std::size_t>(size);
    }
    copied += static_cast<std::uint64_t>(count);
  }
  if (fstat(input.value, &after) || before.st_dev != after.st_dev ||
      before.st_ino != after.st_ino || before.st_uid != after.st_uid ||
      before.st_size != after.st_size ||
      copied != static_cast<std::uint64_t>(before.st_size) ||
      before.st_mtim.tv_sec != after.st_mtim.tv_sec ||
      before.st_mtim.tv_nsec != after.st_mtim.tv_nsec ||
      before.st_ctim.tv_sec != after.st_ctim.tv_sec ||
      before.st_ctim.tv_nsec != after.st_ctim.tv_nsec || fsync(output.value))
    throw HelperFailure("TARGET_VALIDATION_FAILED");
  fs::rename(temporary, destination);
  sync_directory(destination.parent_path());
}
void send_cached(int fd, const fs::path &directory, const json &result) {
  send_header(fd, result);
  std::array<char, 65536> bytes{};
  for (const auto &file : result.at("files")) {
    Descriptor input(
        open((directory / file.at("name").get<std::string>()).c_str(),
             O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
    if (input.value < 0)
      throw HelperFailure("HELPER_NOT_READY");
    std::uint64_t remaining = file.at("size").get<std::uint64_t>();
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(15 + remaining / (1024 * 1024));
    while (remaining) {
      const auto count = read(input.value, bytes.data(),
                              static_cast<std::size_t>(std::min<std::uint64_t>(
                                  remaining, bytes.size())));
      if (count < 0 && errno == EINTR)
        continue;
      if (count <= 0)
        throw HelperFailure("HELPER_PROTOCOL_ERROR");
      send_bytes(
          fd, bytes.data(), static_cast<std::size_t>(count),
          [] { return !helper_alive; }, deadline);
      remaining -= static_cast<std::uint64_t>(count);
    }
  }
}
struct ActiveJob {
  std::string token;
  std::atomic<bool> canceled{false};
};
class HelperService {
public:
  HelperService()
      : catalog_(secret("TASK_KINDS_FILE", "/etc/drop/task-kinds.json")),
        root_(secret("COLLECTOR_HELPER_WORK_DIR", "/var/lib/drop-helper")),
        incoming_root_(secret("COLLECTOR_HELPER_INCOMING_DIR",
                              "/run/drop-helper-incoming")),
        expected_uid_(static_cast<uid_t>(
            std::stoul(secret("COLLECTOR_HELPER_AGENT_UID", "10001")))),
        expected_gid_(static_cast<gid_t>(
            std::stoul(secret("COLLECTOR_HELPER_AGENT_GID", "10001")))),
        disk_limit_(
            std::stoull(secret("COLLECTOR_HELPER_DISK_BYTES", "2147483648"))),
        retention_(
            std::stoll(secret("COLLECTOR_HELPER_RETENTION_SECONDS", "86400"))) {
    if (geteuid() != 0 || !expected_uid_ || getegid() != expected_gid_ ||
        root_.empty() || !root_.is_absolute() || root_ == root_.root_path() ||
        disk_limit_ < 128ULL * 1024 * 1024 ||
        disk_limit_ > 8ULL * 1024 * 1024 * 1024 || retention_ < 300 ||
        retention_ > 604800 || secret("ALLOWED_TARGET_UIDS").empty() ||
        !incoming_root_.is_absolute() ||
        incoming_root_ == incoming_root_.root_path())
      throw HelperFailure("INVALID_HELPER_CONFIGURATION");
    fs::create_directories(root_);
    struct stat info {};
    if (lstat(root_.c_str(), &info) || !S_ISDIR(info.st_mode) ||
        info.st_uid != 0)
      throw HelperFailure("INVALID_HELPER_CONFIGURATION");
    chmod(root_.c_str(), 0700);
    fs::create_directories(incoming_root_);
    const auto private_path = fs::canonical(root_);
    const auto receive_path = fs::canonical(incoming_root_);
    const auto contains = [](const fs::path &parent, const fs::path &child) {
      return std::mismatch(parent.begin(), parent.end(), child.begin(),
                           child.end())
                 .first == parent.end();
    };
    if (contains(private_path, receive_path) ||
        contains(receive_path, private_path) ||
        lstat(incoming_root_.c_str(), &info) || !S_ISDIR(info.st_mode) ||
        info.st_uid != 0 ||
        (info.st_gid != expected_gid_ &&
         chown(incoming_root_.c_str(), static_cast<uid_t>(-1),
               expected_gid_)) ||
        chmod(incoming_root_.c_str(), 01770))
      throw HelperFailure("INVALID_HELPER_CONFIGURATION");
    const auto bounded = secret("COLLECTOR_HELPER_REQUIRE_BOUNDED_FS", "true");
    struct stat private_stat {
    }, receive_stat{};
    if ((bounded != "true" && bounded != "false") ||
        stat(root_.c_str(), &private_stat) ||
        stat(incoming_root_.c_str(), &receive_stat) ||
        (bounded == "true" && (private_stat.st_dev != receive_stat.st_dev ||
                               fs::space(root_).capacity > disk_limit_)))
      throw HelperFailure("INVALID_HELPER_CONFIGURATION");
    bounded_filesystem_ = bounded == "true";
    registry_ =
        std::make_unique<RunnerRegistry>(catalog_, root_.string(), perf_path());
    details_ = registry_->capabilities();
    for (auto &entry : details_.items())
      if (!helper_kind(entry.key()))
        entry.value()["available"] = false;
    for (auto &entry : details_.items()) {
      if (!cooperative_kind(entry.key()))
        continue;
      entry.value()["cooperative_output_group"] = expected_gid_;
      entry.value()["shared_receive_mount_required"] = true;
      if (!has_read_search_capability()) {
        entry.value()["available"] = false;
        entry.value()["reason"] = "CAP_DAC_READ_SEARCH is required to freeze "
                                  "target-owned private profiler output";
      }
    }
    // A real, self-targeted perf record verifies kernel policy independently of
    // tool installation. It never samples a production PID.
    if (details_.at("PERF_CPU").value("available", false)) {
      const auto path = root_ / "startup-perf.data";
      auto argv =
          perf_record_argv(SamplingParameters{getpid(), 1, 1, "process"},
                           path.string(), getpid());
      argv[0] = registry_->tool("perf").path;
      const auto probe =
          execute(argv, (root_ / "startup-perf.log").string(), 4s);
      const auto available = probe.exit_code == 0 &&
                             fs::is_regular_file(path) &&
                             fs::file_size(path) > 0;
      details_["PERF_CPU"]["available"] = available;
      details_["PERF_CPU"]["reason"] =
          available ? ""
                    : "Real helper perf_event_open probe refused by current "
                      "kernel/container policy";
      fs::remove(path);
      fs::remove(root_ / "startup-perf.log");
    }
    clean(true);
  }
  gid_t group() const { return expected_gid_; }
  void handle(int socket_fd) {
    Descriptor connection(socket_fd);
    bool response_started = false;
    try {
      ucred peer{};
      socklen_t length = sizeof(peer);
      if (getsockopt(socket_fd, SOL_SOCKET, SO_PEERCRED, &peer, &length) ||
          peer.uid != expected_uid_ || peer.pid <= 1)
        throw HelperFailure("HELPER_PEER_DENIED");
      const auto request = read_header(
          socket_fd, [] { return !helper_alive; },
          std::chrono::steady_clock::now() + 3s);
      if (!request.is_object() || request.value("protocol_version", 0) != 1)
        throw HelperFailure("INVALID_HELPER_REQUEST");
      const auto operation = request.value("op", "");
      if (operation == "capabilities") {
        keys(request, {"protocol_version", "op"});
        send_header(socket_fd,
                    {{"protocol_version", 1},
                     {"ok", true},
                     {"capabilities", details_},
                     {"helper_uid", 0},
                     {"agent_uid", expected_uid_},
                     {"disk_limit_bytes", disk_limit_},
                     {"bounded_filesystem", bounded_filesystem_},
                     {"filesystem_capacity_bytes", fs::space(root_).capacity}});
        return;
      }
      if (operation == "cancel") {
        keys(request, {"protocol_version", "op", "token"});
        const auto token = request.value("token", "");
        if (!valid_helper_token(token))
          throw HelperFailure("INVALID_HELPER_REQUEST");
        std::lock_guard lock(mutex_);
        if (active_ && active_->token == token)
          active_->canceled = true;
        send_header(socket_fd, {{"protocol_version", 1}, {"ok", true}});
        return;
      }
      if (operation == "ack" || operation == "resume") {
        keys(request, {"protocol_version", "op", "token"});
        const auto token = request.value("token", "");
        if (!valid_helper_token(token))
          throw HelperFailure("INVALID_HELPER_REQUEST");
        const auto directory = root_ / token;
        json result;
        {
          std::lock_guard lock(mutex_);
          if (active_ && active_->token == token)
            throw HelperFailure("HELPER_BUSY");
          result = cached(directory);
          if (operation == "ack") {
            check_response(result);
            if (result.value("state", "") != "ACKED") {
              auto tombstone = result;
              tombstone["state"] = "ACKED";
              tombstone["metadata"] = json::object();
              atomic_write((directory / "result.json").string(),
                           tombstone.dump());
              sync_directory(directory);
            }
            for (const auto &file : result.at("files"))
              fs::remove(directory / file.at("name").get<std::string>());
            sync_directory(directory);
            send_header(socket_fd, {{"protocol_version", 1}, {"ok", true}});
            return;
          }
        }
        if (result.value("state", "") == "ACKED")
          throw HelperFailure("HELPER_ACKNOWLEDGED");
        check_response(result);
        response_started = true;
        send_cached(socket_fd, directory, result);
        return;
      }
      const auto job = decode_helper_request(request, catalog_);
      const auto directory = root_ / job.token;
      const auto canonical = encode_helper_request(job).dump();
      auto active = std::make_shared<ActiveJob>();
      active->token = job.token;
      std::uint64_t effective_file_limit = 0;
      std::optional<json> replay;
      {
        std::lock_guard lock(mutex_);
        if (active_)
          throw HelperFailure("HELPER_BUSY");
        clean(false);
        if (fs::exists(directory)) {
          if (fs::is_symlink(directory) ||
              read_file((directory / "request.json").string(), header_limit) !=
                  canonical)
            throw HelperFailure("INVALID_HELPER_REQUEST");
          const auto result = cached(directory);
          if (result.value("state", "") == "ACKED")
            throw HelperFailure("HELPER_ACKNOWLEDGED");
          check_response(result);
          replay = result;
        } else {
          // Profiler intermediates and the final RAW may coexist. Reserve all
          // four bounded files before admitting work, independently of Agent.
          const auto used = bytes_used();
          const auto fixed = job.binary_max + 1024 * 1024;
          if (fixed >= disk_limit_ || used >= disk_limit_ - fixed)
            throw HelperFailure("DISK_BUDGET_EXCEEDED");
          effective_file_limit = job.context.max_file_bytes;
          const auto estimated = 4 * effective_file_limit + fixed;
          if (effective_file_limit < 1024 || estimated > disk_limit_ ||
              used > disk_limit_ - estimated ||
              fs::space(root_).available < estimated + 256ULL * 1024 * 1024)
            throw HelperFailure("DISK_BUDGET_EXCEEDED");
          fs::create_directory(directory);
          chmod(directory.c_str(), 0700);
          atomic_write((directory / "request.json").string(), canonical);
          atomic_write((directory / "result.json").string(),
                       json{{"protocol_version", 1},
                            {"ok", false},
                            {"state", "RUNNING"},
                            {"error_code", "HELPER_NOT_READY"},
                            {"expires_unix_ms", now_ms() + retention_ * 1000}}
                           .dump());
          sync_directory(directory);
          sync_directory(root_);
          active_ = active;
        }
      }
      if (replay) {
        response_started = true;
        send_cached(socket_fd, directory, *replay);
        return;
      }
      ScopeExit reset_active{[this, active] {
        std::lock_guard lock(mutex_);
        if (active_ == active)
          active_.reset();
      }};
      json result;
      std::atomic<bool> budget_exceeded{false};
      try {
        auto context = job.context;
        context.directory = directory;
        context.max_file_bytes = effective_file_limit;
        context.limits.file_bytes = effective_file_limit;
        if (job.controlled_pid && !descendant(job.controlled_pid, peer.pid))
          throw HelperFailure("TARGET_VALIDATION_FAILED");
        ProcessIdentity actual;
        try {
          actual = resolve_target(target_from_json(context.parameters),
                                  target_policy(job.controlled_pid),
                                  directory.string());
        } catch (...) {
          throw HelperFailure("TARGET_VALIDATION_FAILED");
        }
        // Executable paths are never accepted through IPC. The immutable
        // device/inode identity is pinned instead, then the helper resolves
        // its own trusted path from procfs for runner use.
        context.target.executable = actual.executable;
        if (!same_identity(actual, context.target))
          throw HelperFailure("TARGET_VALIDATION_FAILED");
        context.target = actual;
        context.canceled = [&, active] {
          if (!helper_alive || disconnected(socket_fd))
            active->canceled = true;
          return active->canceled.load();
        };
        if (!details_.at(context.kind).value("available", false))
          throw HelperFailure("CAPABILITY_UNAVAILABLE");
        IncomingDirectory incoming;
        if (cooperative_kind(context.kind)) {
          if (!process_in_group(actual.pid, expected_gid_))
            throw HelperFailure("CAPABILITY_UNAVAILABLE");
          if (fs::space(incoming_root_).available <
              context.max_file_bytes + 1024 * 1024)
            throw HelperFailure("DISK_BUDGET_EXCEEDED");
          incoming.path = incoming_root_ / job.token;
          if (!fs::create_directory(incoming.path) ||
              chmod(incoming.path.c_str(), 01770))
            throw HelperFailure("DISK_BUDGET_EXCEEDED");
          context.output_directory = incoming.path;
        }
        const auto job_budget =
            4 * context.max_file_bytes + job.binary_max + 1024 * 1024;
        std::jthread monitor([&, receive =
                                     incoming.path](std::stop_token stopped) {
          while (!stopped.stop_requested()) {
            try {
              std::uint64_t bytes = 0, entries = 0;
              for (const auto &base : {directory, receive}) {
                if (base.empty())
                  continue;
                for (const auto &entry : fs::directory_iterator(base)) {
                  struct stat status {};
                  if (lstat(entry.path().c_str(), &status)) {
                    if (errno == ENOENT)
                      continue; // Atomic journal replacement/cleanup.
                    throw HelperFailure("DISK_BUDGET_EXCEEDED");
                  }
                  if (++entries > 64 || !S_ISREG(status.st_mode) ||
                      status.st_size < 0 ||
                      static_cast<std::uint64_t>(status.st_size) >
                          job_budget - bytes)
                    throw HelperFailure("DISK_BUDGET_EXCEEDED");
                  const auto size = static_cast<std::uint64_t>(status.st_size);
                  bytes += size;
                  if (base == receive && size > context.max_file_bytes)
                    throw HelperFailure("DISK_BUDGET_EXCEEDED");
                }
              }
              if (fs::space(root_).available < 64ULL * 1024 * 1024)
                throw HelperFailure("DISK_BUDGET_EXCEEDED");
            } catch (...) {
              budget_exceeded = true;
              active->canceled = true;
              return;
            }
            for (unsigned interval = 0;
                 interval < 10 && !stopped.stop_requested(); ++interval)
              std::this_thread::sleep_for(10ms);
          }
        });
        const auto start = now_ms();
        auto output = registry_->collect(context);
        const auto finish = now_ms();
        if (!same_identity(actual, process_identity(actual.pid)))
          throw HelperFailure("TARGET_VALIDATION_FAILED");
        if (fs::file_size(output.path) > context.max_file_bytes)
          throw HelperFailure("DISK_BUDGET_EXCEEDED");
        if (!incoming.path.empty()) {
          freeze_cooperative_output(output.path, directory / output.name,
                                    actual.uid, context.max_file_bytes);
          output.path = directory / output.name;
          if (!same_identity(actual, process_identity(actual.pid)))
            throw HelperFailure("TARGET_VALIDATION_FAILED");
        }
        output.metadata["collection_started_unix_ms"] = start;
        output.metadata["collection_finished_unix_ms"] = finish;
        output.metadata["collection_isolation"] = {
            {"agent_uid", peer.uid},
            {"helper_uid", 0},
            {"transport", "unix_peer_credentials"},
            {"effective_file_limit_bytes", effective_file_limit},
            {"bounded_filesystem", bounded_filesystem_},
            {"cooperative_output_group", expected_gid_}};
        json files =
            json::array({file_manifest(output.path, output.name, "RAW")});
        if (job.binary_max) {
          copy_target_binary(actual, directory / "target.elf", job.binary_max);
          if (read_file((directory / "target.elf").string(), 4) !=
                  std::string("\x7f"
                              "ELF",
                              4) ||
              !same_identity(actual, process_identity(actual.pid)))
            throw HelperFailure("TARGET_VALIDATION_FAILED");
          files.push_back(
              file_manifest(directory / "target.elf", "target.elf", "BINARY"));
          if (output.metadata.contains("binary_sha256") &&
              output.metadata.at("binary_sha256") != files.back().at("sha256"))
            throw HelperFailure("TARGET_VALIDATION_FAILED");
        }
        result = {{"protocol_version", 1},
                  {"ok", true},
                  {"state", "READY"},
                  {"kind", context.kind},
                  {"name", output.name},
                  {"format", output.format},
                  {"content_type", output.content_type},
                  {"metadata", output.metadata},
                  {"files", files},
                  {"expires_unix_ms", now_ms() + retention_ * 1000}};
      } catch (const std::exception &error) {
        // Private root-only troubleshooting, never part of IPC, Agent logs,
        // manifests or uploaded artifacts. Inputs contain no storage grants.
        try {
          const std::string detail = error.what();
          static const std::set<std::string> safe_details{
              "Could not read file", "RUNNER_TIMEOUT", "TARGET_CHANGED", "TASK_CANCELED",
              "NO_TRACE_EVENTS", "Trace stack exceeds depth budget", "Trace event exceeds budget",
              "Trace event identity/schema mismatch", "Trace stack record is truncated",
              "Trace stack record is incomplete", "TOOL_INTEGRITY_CHANGED", "TOOL_SUPPORT_INTEGRITY_CHANGED"};
          atomic_write(
              (directory / "failure-detail.json").string(),
              json{{"message", safe_details.contains(detail) ? detail : "COLLECTION_FAILED"}}
                  .dump());
        } catch (...) {
        }
        const auto *helper = dynamic_cast<const HelperFailure *>(&error);
        const auto *profiler =
            dynamic_cast<const ProfilerExecutionFailure *>(&error);
        result = {{"protocol_version", 1},
                  {"ok", false},
                  {"state", "FAILED"},
                  {"error_code",
                   budget_exceeded    ? "DISK_BUDGET_EXCEEDED"
                   : helper           ? helper->error_code
                   : profiler         ? profiler->error_code
                   : active->canceled ? "TASK_CANCELED"
                   : std::string(error.what()) == "TOOL_INTEGRITY_CHANGED"
                       ? "TOOL_INTEGRITY_CHANGED"
                   : std::string(error.what()) == "TARGET_CHANGED"
                       ? "TARGET_CHANGED"
                       : "RUNNER_FAILED"},
                  {"expires_unix_ms", now_ms() + retention_ * 1000}};
      }
      {
        std::lock_guard lock(mutex_);
        atomic_write((directory / "result.json").string(), result.dump());
        sync_directory(directory);
        active_.reset();
      }
      response_started = true;
      if (result.value("ok", false))
        send_cached(socket_fd, directory, result);
      else
        send_header(socket_fd, result);
    } catch (const std::exception &error) {
      if (!response_started) {
        const auto *failure = dynamic_cast<const HelperFailure *>(&error);
        try {
          send_header(socket_fd,
                      {{"protocol_version", 1},
                       {"ok", false},
                       {"error_code", failure ? failure->error_code
                                              : "INVALID_HELPER_REQUEST"}});
        } catch (...) {
        }
      }
    }
  }

private:
  json cached(const fs::path &directory) const {
    if (fs::is_symlink(directory) ||
        !fs::is_regular_file(directory / "result.json"))
      throw HelperFailure("HELPER_NOT_READY");
    auto result = parse_json(
        read_file((directory / "result.json").string(), header_limit),
        header_limit);
    if (!result.is_object() || result.value("protocol_version", 0) != 1 ||
        !result.contains("ok") || !result.at("ok").is_boolean())
      throw HelperFailure("HELPER_PROTOCOL_ERROR");
    const auto state = result.value("state", "");
    if (state != "RUNNING" && state != "FAILED" && state != "READY" &&
        state != "ACKED")
      throw HelperFailure("HELPER_PROTOCOL_ERROR");
    if (result.at("ok") != (state == "READY" || state == "ACKED"))
      throw HelperFailure("HELPER_PROTOCOL_ERROR");
    if (state == "READY" || state == "ACKED") {
      const auto kind = result.value("kind", "");
      if (!helper_kind(kind) || !result.at("files").is_array() ||
          result.at("files").empty() || result.at("files").size() > 2 ||
          !result.at("metadata").is_object())
        throw HelperFailure("HELPER_PROTOCOL_ERROR");
      const auto &spec = catalog_.get(kind);
      for (std::size_t index = 0; index < result.at("files").size(); ++index) {
        const auto &file = result.at("files").at(index);
        keys(file, {"name", "kind", "size", "sha256"});
        const auto name = file.value("name", "");
        const auto raw_name = spec.at("raw_name").get<std::string>();
        if (file.value("kind", "") != (index ? "BINARY" : "RAW") ||
            (index ? (kind != "BOLT_PROFILE" || name != "target.elf")
                   : (name != raw_name &&
                      !(kind == "PERF_CPU" && name == "profile.folded"))) ||
            !valid_helper_token(file.value("sha256", "")))
          throw HelperFailure("HELPER_PROTOCOL_ERROR");
        positive(file, "size",
                 index ? absolute_file_limit
                       : spec.at("max_file_bytes").get<std::uint64_t>());
      }
    }
    return result;
  }
  std::uint64_t bytes_used() const {
    std::uint64_t total = 0, entries = 0;
    for (const auto &base : {root_, incoming_root_})
      for (const auto &entry : fs::recursive_directory_iterator(base)) {
        if (++entries > 100000)
          throw HelperFailure("DISK_BUDGET_EXCEEDED");
        if (entry.is_symlink())
          throw HelperFailure("DISK_BUDGET_EXCEEDED");
        if (entry.is_regular_file()) {
          const auto size = entry.file_size();
          if (size > disk_limit_ || total > disk_limit_ - size)
            return disk_limit_;
          total += size;
        }
      }
    return total;
  }
  void clean(bool startup) {
    unsigned count = 0;
    for (const auto &entry : fs::directory_iterator(root_)) {
      if (++count > 10000)
        throw HelperFailure("DISK_BUDGET_EXCEEDED");
      const auto token = entry.path().filename().string();
      if (!valid_helper_token(token) || entry.is_symlink() ||
          !entry.is_directory() || (active_ && active_->token == token))
        continue;
      try {
        auto result = cached(entry.path());
        if (startup && result.value("state", "") == "RUNNING") {
          result["state"] = "FAILED";
          result["ok"] = false;
          result["error_code"] = "HELPER_RESTARTED";
          atomic_write((entry.path() / "result.json").string(), result.dump());
        }
        if (result.value("state", "") != "RUNNING" &&
            result.at("expires_unix_ms").get<std::int64_t>() < now_ms()) {
          fs::remove_all(incoming_root_ / token);
          fs::remove_all(entry.path());
          sync_directory(incoming_root_);
          sync_directory(root_);
        }
      } catch (...) { /* Corrupt journals retain bytes and consume admission
                         budget. */
      }
    }
  }
  Catalog catalog_;
  fs::path root_;
  fs::path incoming_root_;
  uid_t expected_uid_;
  gid_t expected_gid_;
  std::uint64_t disk_limit_;
  std::int64_t retention_;
  bool bounded_filesystem_ = false;
  std::unique_ptr<RunnerRegistry> registry_;
  json details_;
  std::mutex mutex_;
  std::shared_ptr<ActiveJob> active_;
};
} // namespace

bool helper_kind(const std::string &kind) {
  static const std::set<std::string> kinds{"PERF_CPU",   "BOLT_PROFILE",
                                           "IO_TRACE",   "MEMORY_TRACE",
                                           "PYTHON_CPU", "PYTHON_MEMORY"};
  return kinds.contains(kind);
}
bool valid_helper_token(const std::string &token) {
  return token.size() == 64 &&
         token.find_first_not_of("0123456789abcdef") == std::string::npos;
}
HelperFailure::HelperFailure(std::string code)
    : std::runtime_error("Local collector helper refused or interrupted the "
                         "bounded operation"),
      error_code(std::move(code)) {}

json encode_helper_request(const HelperRequest &request) {
  const auto &context = request.context;
  return {{"protocol_version", 1},
          {"op", "collect"},
          {"token", request.token},
          {"kind", context.kind},
          {"parameters", context.parameters},
          {"collection_protocol", context.protocol_version},
          {"controlled_pid", request.controlled_pid},
          {"binary_max", request.binary_max},
          {"max_file_bytes", context.max_file_bytes},
          {"budget",
           {{"address_space_bytes", context.limits.address_space_bytes},
            {"cpu_seconds", context.limits.cpu_seconds},
            {"processes", context.limits.processes},
            {"cpu_affinity", context.limits.cpu_affinity}}},
          {"target_identity",
           {{"pid", context.target.pid},
            {"uid", context.target.uid},
            {"start_time_ticks", context.target.start_time_ticks},
            {"pid_namespace", context.target.pid_namespace},
            {"namespace_pid", context.target.namespace_pid},
            {"executable_device", context.target.executable_device},
            {"executable_inode", context.target.executable_inode}}}};
}
HelperRequest decode_helper_request(const json &value, const Catalog &catalog) {
  keys(value, {"protocol_version", "op", "token", "kind", "parameters",
               "collection_protocol", "controlled_pid", "binary_max",
               "max_file_bytes", "budget", "target_identity"});
  if (value.dump().size() > header_limit || value.at("protocol_version") != 1 ||
      value.at("op") != "collect" ||
      !valid_helper_token(value.value("token", "")) ||
      !helper_kind(value.value("kind", "")))
    throw HelperFailure("INVALID_HELPER_REQUEST");
  HelperRequest request;
  request.token = value.at("token");
  auto &context = request.context;
  context.kind = value.at("kind");
  context.parameters = value.at("parameters");
  catalog.validate(context.kind, context.parameters);
  context.options = context.parameters.value("options", json::object());
  context.protocol_version =
      static_cast<int>(positive(value, "collection_protocol", 2));
  if (context.protocol_version == 1 && context.kind != "PERF_CPU")
    throw HelperFailure("INVALID_HELPER_REQUEST");
  const auto &spec = catalog.get(context.kind);
  context.max_file_bytes =
      positive(value, "max_file_bytes", spec.at("max_file_bytes"));
  if (context.max_file_bytes < 1024)
    throw HelperFailure("INVALID_HELPER_REQUEST");
  context.duration_seconds = context.parameters.value(
      "duration_seconds", spec.value("default_duration_seconds", 10));
  context.frequency_hz = context.parameters.value(
      "frequency_hz", spec.value("default_frequency_hz", 99));
  request.controlled_pid =
      static_cast<int>(positive(value, "controlled_pid", 4194304, true));
  request.binary_max = positive(value, "binary_max", absolute_file_limit, true);
  if ((request.binary_max && context.kind != "BOLT_PROFILE") ||
      (context.kind == "BOLT_PROFILE" && !request.binary_max))
    throw HelperFailure("INVALID_HELPER_REQUEST");
  const auto &budget = value.at("budget");
  keys(budget,
       {"address_space_bytes", "cpu_seconds", "processes", "cpu_affinity"});
  context.limits.address_space_bytes =
      positive(budget, "address_space_bytes", 8ULL * 1024 * 1024 * 1024);
  context.limits.cpu_seconds =
      static_cast<unsigned>(positive(budget, "cpu_seconds", 300));
  context.limits.processes =
      static_cast<unsigned>(positive(budget, "processes", 64));
  if (!budget.at("cpu_affinity").is_array() ||
      budget.at("cpu_affinity").size() > 64)
    throw HelperFailure("INVALID_HELPER_REQUEST");
  std::set<unsigned> cpus;
  for (const auto &cpu : budget.at("cpu_affinity")) {
    if (!cpu.is_number_integer() || cpu < 0 || cpu >= 1024 ||
        !cpus.insert(cpu.get<unsigned>()).second)
      throw HelperFailure("INVALID_HELPER_REQUEST");
    context.limits.cpu_affinity.push_back(cpu.get<unsigned>());
  }
  context.limits.file_bytes = context.max_file_bytes;
  const auto &identity = value.at("target_identity");
  keys(identity, {"pid", "uid", "start_time_ticks", "pid_namespace",
                  "namespace_pid", "executable_device", "executable_inode"});
  context.target.pid = static_cast<int>(positive(identity, "pid", 4194304));
  if (context.target.pid <= 1)
    throw HelperFailure("INVALID_HELPER_REQUEST");
  context.target.uid =
      static_cast<unsigned>(positive(identity, "uid", 4294967295ULL, true));
  context.target.start_time_ticks =
      positive(identity, "start_time_ticks", INT64_MAX);
  context.target.namespace_pid =
      static_cast<int>(positive(identity, "namespace_pid", 4194304));
  context.target.pid_namespace =
      identity.at("pid_namespace").get<std::string>();
  if (!std::regex_match(context.target.pid_namespace,
                        std::regex("pid:\\[[0-9]{1,20}\\]")))
    throw HelperFailure("INVALID_HELPER_REQUEST");
  context.target.executable_device =
      positive(identity, "executable_device", INT64_MAX);
  context.target.executable_inode =
      positive(identity, "executable_inode", INT64_MAX);
  return request;
}

HelperClient::HelperClient(std::string socket_path, const Catalog &catalog)
    : socket_path_(std::move(socket_path)), catalog_(catalog) {
  address(socket_path_);
}
json HelperClient::capabilities() const {
  Descriptor connection(connect_helper(socket_path_));
  send_header(connection.value,
              {{"protocol_version", 1}, {"op", "capabilities"}});
  auto response = read_header(
      connection.value, [] { return false; },
      std::chrono::steady_clock::now() + 6s);
  check_response(response);
  if (!response.at("capabilities").is_object())
    throw HelperFailure("HELPER_PROTOCOL_ERROR");
  return response;
}
RunnerOutput HelperClient::collect(const HelperRequest &request,
                                   bool resume_only) const {
  (void)decode_helper_request(encode_helper_request(request), catalog_);
  Descriptor connection(connect_helper(socket_path_));
  send_header(connection.value, resume_only ? json{{"protocol_version", 1},
                                                   {"op", "resume"},
                                                   {"token", request.token}}
                                            : encode_helper_request(request));
  bool cancel_sent = false;
  const auto canceled = [&] {
    if (!request.context.canceled())
      return false;
    if (!cancel_sent) {
      cancel_sent = true;
      try {
        Descriptor cancellation(connect_helper(socket_path_));
        send_header(cancellation.value, {{"protocol_version", 1},
                                         {"op", "cancel"},
                                         {"token", request.token}});
      } catch (...) {
      }
    }
    return true;
  };
  const auto response = read_header(
      connection.value, canceled,
      std::chrono::steady_clock::now() +
          std::chrono::seconds(request.context.duration_seconds + 90));
  check_response(response);
  const auto &spec = catalog_.get(request.context.kind);
  const auto expected_name = request.context.kind == "PERF_CPU" &&
                                     request.context.protocol_version == 1
                                 ? "profile.folded"
                                 : spec.at("raw_name").get<std::string>();
  const auto expected_format = request.context.kind == "PERF_CPU" &&
                                       request.context.protocol_version == 1
                                   ? "folded"
                                   : spec.at("input_format").get<std::string>();
  if (response.value("kind", "") != request.context.kind ||
      response.value("name", "") != expected_name ||
      response.value("format", "") != expected_format ||
      response.value("content_type", "") !=
          spec.at("raw_content_type").get<std::string>() ||
      !response.at("metadata").is_object() ||
      !response.at("files").is_array() ||
      response.at("files").size() != (request.binary_max ? 2 : 1))
    throw HelperFailure("HELPER_PROTOCOL_ERROR");
  for (std::size_t index = 0; index < response.at("files").size(); ++index) {
    const auto &file = response.at("files").at(index);
    keys(file, {"name", "kind", "size", "sha256"});
    const auto maximum =
        index ? request.binary_max : request.context.max_file_bytes;
    positive(file, "size", maximum);
    if (file.value("name", "") != (index ? "target.elf" : expected_name) ||
        file.value("kind", "") != (index ? "BINARY" : "RAW") ||
        !valid_helper_token(file.value("sha256", "")))
      throw HelperFailure("HELPER_PROTOCOL_ERROR");
    save_file(connection.value, request.context.directory, file, canceled);
  }
  return {request.context.directory / expected_name, expected_name,
          expected_format, spec.at("raw_content_type"),
          response.at("metadata")};
}
void HelperClient::acknowledge(const std::string &token) const {
  if (!valid_helper_token(token))
    throw HelperFailure("INVALID_HELPER_REQUEST");
  Descriptor connection(connect_helper(socket_path_));
  send_header(connection.value,
              {{"protocol_version", 1}, {"op", "ack"}, {"token", token}});
  check_response(read_header(
      connection.value, [] { return false; },
      std::chrono::steady_clock::now() + 3s));
}

int run_collector_helper() {
  std::signal(SIGTERM, helper_stop);
  std::signal(SIGINT, helper_stop);
  umask(0077);
  HelperService service;
  const auto path = fs::path(
      secret("COLLECTOR_HELPER_SOCKET", "/run/drop-helper/control.sock"));
  const auto endpoint = address(path.string());
  fs::create_directories(path.parent_path());
  struct stat directory {};
  if (lstat(path.parent_path().c_str(), &directory) ||
      !S_ISDIR(directory.st_mode) || directory.st_uid != 0)
    throw HelperFailure("INVALID_HELPER_CONFIGURATION");
  // A fresh named volume starts root:root. Its owner can select its own
  // effective group without CAP_CHOWN; no privilege is added for bootstrap.
  if ((directory.st_gid != service.group() &&
       chown(path.parent_path().c_str(), static_cast<uid_t>(-1),
             service.group())) ||
      chmod(path.parent_path().c_str(), 0750))
    throw HelperFailure("INVALID_HELPER_CONFIGURATION");
  struct stat previous {};
  if (lstat(path.c_str(), &previous) == 0) {
    if (!S_ISSOCK(previous.st_mode) || previous.st_uid != 0)
      throw HelperFailure("INVALID_HELPER_CONFIGURATION");
    fs::remove(path);
  }
  Descriptor listener(socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0));
  if (listener.value < 0 ||
      bind(listener.value, reinterpret_cast<const sockaddr *>(&endpoint),
           sizeof(endpoint)) ||
      chmod(path.c_str(), 0660) || listen(listener.value, 4))
    throw HelperFailure("INVALID_HELPER_CONFIGURATION");
  log_configuration("drop-collector-helper");
  std::vector<std::future<void>> handlers;
  while (helper_alive) {
    for (auto item = handlers.begin(); item != handlers.end();)
      if (item->wait_for(0s) == std::future_status::ready) {
        item->get();
        item = handlers.erase(item);
      } else
        ++item;
    pollfd input{listener.value, POLLIN, 0};
    if (poll(&input, 1, 100) <= 0 || handlers.size() >= 4)
      continue;
    const auto connection =
        accept4(listener.value, nullptr, nullptr, SOCK_CLOEXEC);
    if (connection >= 0)
      handlers.push_back(std::async(std::launch::async, [&service, connection] {
        service.handle(connection);
      }));
  }
  for (auto &handler : handlers)
    handler.get();
  fs::remove(path);
  return 0;
}
} // namespace dropcore
