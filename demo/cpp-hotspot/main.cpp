#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

// Keep the controlled CPU hotspot in the external dynamic symbol table.  The
// Analyzer runs in another container, so a function hidden inside the
// anonymous namespace is not reliably resolvable from perf.data even when the
// byte-identical ELF image is available.
extern "C" {
#if defined(__GNUC__)
__attribute__((noinline, used, visibility("default")))
#endif
std::uint64_t cpp_cpu_hot_function(std::uint64_t seed) {
  volatile std::uint64_t value = seed;
  for (int index = 0; index < 65'536; ++index) {
    value ^= value << 13;
    value ^= value >> 7;
    value ^= value << 17;
  }
  return value;
}
}

namespace {
using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;

constexpr int kPort = 8084;
constexpr int kMinDurationSeconds = 15;
constexpr int kMaxDurationSeconds = 300;
constexpr int kMinMemoryMegabytes = 16;
constexpr int kMaxMemoryMegabytes = 128;
constexpr int kMinDownstreamDelayMilliseconds = 50;
constexpr int kMaxDownstreamDelayMilliseconds = 1000;
constexpr std::size_t kIoChunkBytes = 128U * 1024U;
constexpr off_t kMaxIoFileBytes = 64LL * 1024LL * 1024LL;
constexpr char kIoFaultPath[] = "/tmp/mini-drop-cpp-io-fault.bin";
constexpr char kApplicationMetricsPath[] =
    "/tmp/mini-drop-app-metrics.json";

std::int64_t now_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             Clock::now().time_since_epoch())
      .count();
}

class FaultSwitch {
 public:
  void start(int duration_seconds) {
    const int bounded =
        std::clamp(duration_seconds, kMinDurationSeconds, kMaxDurationSeconds);
    deadline_ns_.store(
        now_ns() + static_cast<std::int64_t>(bounded) * 1'000'000'000LL,
        std::memory_order_release);
    active_.store(true, std::memory_order_release);
  }

  void stop() {
    active_.store(false, std::memory_order_release);
    deadline_ns_.store(0, std::memory_order_release);
  }

  bool active() {
    if (!active_.load(std::memory_order_acquire)) {
      return false;
    }
    if (now_ns() >= deadline_ns_.load(std::memory_order_acquire)) {
      stop();
      return false;
    }
    return true;
  }

  int remaining_seconds() {
    if (!active()) {
      return 0;
    }
    const auto remaining =
        deadline_ns_.load(std::memory_order_acquire) - now_ns();
    return static_cast<int>(std::max<std::int64_t>(remaining, 0) /
                            1'000'000'000LL);
  }

 private:
  std::atomic<bool> active_{false};
  std::atomic<std::int64_t> deadline_ns_{0};
};

FaultSwitch cpu_fault;
FaultSwitch lock_fault;
FaultSwitch memory_fault;
FaultSwitch io_fault;
FaultSwitch downstream_fault;
std::atomic<std::uint64_t> cpu_operations{0};
std::atomic<std::uint64_t> lock_acquisitions{0};
std::atomic<std::uint64_t> lock_wait_ns{0};
std::atomic<std::uint64_t> io_bytes_written{0};
std::atomic<std::uint64_t> io_operations{0};
std::atomic<std::uint64_t> io_failures{0};
std::atomic<std::uint64_t> downstream_requests{0};
std::atomic<std::uint64_t> downstream_failures{0};
std::atomic<std::uint64_t> downstream_latency_ns{0};
std::atomic<int> memory_target_mb{96};
std::atomic<int> downstream_delay_ms{260};
std::mutex contended_mutex;
std::mutex retained_mutex;
std::mutex io_work_mutex;
std::vector<std::unique_ptr<unsigned char[]>> retained_chunks;

void cpu_worker() {
  std::uint64_t state = 0x9e3779b97f4a7c15ULL;
  while (true) {
    if (!cpu_fault.active()) {
      std::this_thread::sleep_for(20ms);
      continue;
    }
    state = cpp_cpu_hot_function(state);
    cpu_operations.fetch_add(65'536, std::memory_order_relaxed);
  }
}

void lock_owner() {
  while (true) {
    if (!lock_fault.active()) {
      std::this_thread::sleep_for(20ms);
      continue;
    }
    {
      std::lock_guard<std::mutex> guard(contended_mutex);
      std::this_thread::sleep_for(3ms);
    }
    std::this_thread::sleep_for(200us);
  }
}

void lock_waiter() {
  while (true) {
    if (!lock_fault.active()) {
      std::this_thread::sleep_for(20ms);
      continue;
    }
    const auto started = Clock::now();
    {
      std::lock_guard<std::mutex> guard(contended_mutex);
      lock_acquisitions.fetch_add(1, std::memory_order_relaxed);
    }
    const auto waited = std::chrono::duration_cast<std::chrono::nanoseconds>(
                            Clock::now() - started)
                            .count();
    lock_wait_ns.fetch_add(static_cast<std::uint64_t>(std::max<std::int64_t>(
                               waited, 0)),
                           std::memory_order_relaxed);
    std::this_thread::sleep_for(300us);
  }
}

void release_retained_memory() {
  std::lock_guard<std::mutex> guard(retained_mutex);
  retained_chunks.clear();
  retained_chunks.shrink_to_fit();
}

void memory_worker() {
  constexpr std::size_t kChunkBytes = 2U * 1024U * 1024U;
  while (true) {
    if (!memory_fault.active()) {
      release_retained_memory();
      std::this_thread::sleep_for(50ms);
      continue;
    }
    const int target_mb = memory_target_mb.load(std::memory_order_relaxed);
    bool allocate = false;
    {
      std::lock_guard<std::mutex> guard(retained_mutex);
      allocate = static_cast<int>(retained_chunks.size() * 2U) < target_mb;
    }
    if (allocate) {
      auto chunk = std::make_unique<unsigned char[]>(kChunkBytes);
      for (std::size_t offset = 0; offset < kChunkBytes; offset += 4096U) {
        chunk[offset] = static_cast<unsigned char>(offset & 0xffU);
      }
      std::lock_guard<std::mutex> guard(retained_mutex);
      if (memory_fault.active() &&
          static_cast<int>(retained_chunks.size() * 2U) < target_mb) {
        retained_chunks.push_back(std::move(chunk));
      }
    }
    std::this_thread::sleep_for(180ms);
  }
}

std::size_t retained_memory_mb() {
  std::lock_guard<std::mutex> guard(retained_mutex);
  return retained_chunks.size() * 2U;
}

void cleanup_io_fault_file() {
  std::lock_guard<std::mutex> guard(io_work_mutex);
  ::unlink(kIoFaultPath);
}

void io_worker() {
  std::array<unsigned char, kIoChunkBytes> block{};
  for (std::size_t index = 0; index < block.size(); ++index) {
    block[index] = static_cast<unsigned char>((index * 31U) & 0xffU);
  }
  bool was_active = false;
  while (true) {
    const bool active = io_fault.active();
    if (!active) {
      if (was_active) {
        cleanup_io_fault_file();
      }
      was_active = false;
      std::this_thread::sleep_for(50ms);
      continue;
    }
    was_active = true;
    bool failed = false;
    {
      std::lock_guard<std::mutex> guard(io_work_mutex);
      if (!io_fault.active()) {
        continue;
      }
      const int file = ::open(kIoFaultPath, O_CREAT | O_WRONLY | O_APPEND,
                              S_IRUSR | S_IWUSR);
      if (file < 0) {
        failed = true;
      } else {
        std::size_t offset = 0;
        while (offset < block.size()) {
          const ssize_t count =
              ::write(file, block.data() + offset, block.size() - offset);
          if (count <= 0) {
            failed = true;
            break;
          }
          offset += static_cast<std::size_t>(count);
        }
        if (!failed && ::fdatasync(file) != 0) {
          failed = true;
        }
        const off_t size = ::lseek(file, 0, SEEK_END);
        ::close(file);
        if (!failed) {
          io_bytes_written.fetch_add(offset, std::memory_order_relaxed);
          io_operations.fetch_add(1, std::memory_order_relaxed);
        }
        if (size >= kMaxIoFileBytes) {
          ::unlink(kIoFaultPath);
        }
      }
    }
    if (failed) {
      io_failures.fetch_add(1, std::memory_order_relaxed);
      io_fault.stop();
    }
    std::this_thread::sleep_for(15ms);
  }
}

bool call_slow_loopback() {
  const int client = ::socket(AF_INET, SOCK_STREAM, 0);
  if (client < 0) {
    return false;
  }
  timeval timeout{};
  timeout.tv_sec = 2;
  setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(kPort);
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  bool ok = ::connect(client, reinterpret_cast<sockaddr*>(&address),
                      sizeof(address)) == 0;
  constexpr char request[] =
      "GET /work HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
  if (ok) {
    ok = ::send(client, request, sizeof(request) - 1U, MSG_NOSIGNAL) > 0;
  }
  if (ok) {
    char response[256];
    ok = ::recv(client, response, sizeof(response), 0) > 0;
  }
  ::close(client);
  return ok;
}

void downstream_worker() {
  while (true) {
    if (!downstream_fault.active()) {
      std::this_thread::sleep_for(30ms);
      continue;
    }
    const auto started = Clock::now();
    const bool ok = call_slow_loopback();
    const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
                             Clock::now() - started)
                             .count();
    downstream_requests.fetch_add(1, std::memory_order_relaxed);
    downstream_latency_ns.fetch_add(
        static_cast<std::uint64_t>(std::max<std::int64_t>(elapsed, 0)),
        std::memory_order_relaxed);
    if (!ok) {
      downstream_failures.fetch_add(1, std::memory_order_relaxed);
    }
    std::this_thread::sleep_for(20ms);
  }
}

double rss_megabytes() {
  std::ifstream statm("/proc/self/statm");
  long total_pages = 0;
  long resident_pages = 0;
  if (!(statm >> total_pages >> resident_pages)) {
    return 0.0;
  }
  const long page_size = sysconf(_SC_PAGESIZE);
  return static_cast<double>(resident_pages) * static_cast<double>(page_size) /
         (1024.0 * 1024.0);
}

long host_pid() {
  std::ifstream status("/proc/self/status");
  std::string line;
  while (std::getline(status, line)) {
    if (line.rfind("NSpid:", 0) != 0) {
      continue;
    }
    std::istringstream values(line.substr(6));
    long outer_pid = 0;
    if (values >> outer_pid) {
      return outer_pid;
    }
  }
  return static_cast<long>(getpid());
}

int json_integer(const std::string& body, const std::string& key,
                 int fallback) {
  const std::string quoted = "\"" + key + "\"";
  const auto key_position = body.find(quoted);
  if (key_position == std::string::npos) {
    return fallback;
  }
  const auto colon = body.find(':', key_position + quoted.size());
  if (colon == std::string::npos) {
    return fallback;
  }
  const auto begin = body.find_first_of("-0123456789", colon + 1);
  if (begin == std::string::npos) {
    return fallback;
  }
  try {
    return std::stoi(body.substr(begin));
  } catch (...) {
    return fallback;
  }
}

std::string snapshot_json() {
  const bool cpu_active = cpu_fault.active();
  const bool lock_active = lock_fault.active();
  const bool memory_active = memory_fault.active();
  const bool io_active = io_fault.active();
  const bool downstream_active = downstream_fault.active();
  std::ostringstream output;
  output << std::fixed << std::setprecision(2)
         << "{\"runtime\":\"C++\",\"pid\":" << getpid()
         << ",\"host_pid\":" << host_pid()
         << ",\"fault_active\":" << (cpu_active ? "true" : "false")
         << ",\"cpu_fault_active\":" << (cpu_active ? "true" : "false")
         << ",\"lock_fault_active\":" << (lock_active ? "true" : "false")
         << ",\"memory_fault_active\":"
         << (memory_active ? "true" : "false")
         << ",\"io_fault_active\":" << (io_active ? "true" : "false")
         << ",\"downstream_fault_active\":"
         << (downstream_active ? "true" : "false")
         << ",\"cpu_operations\":" << cpu_operations.load()
         << ",\"lock_acquisitions\":" << lock_acquisitions.load()
         << ",\"lock_wait_ms\":"
         << static_cast<double>(lock_wait_ns.load()) / 1'000'000.0
         << ",\"retained_memory_mb\":" << retained_memory_mb()
         << ",\"io_bytes_written\":" << io_bytes_written.load()
         << ",\"io_operations\":" << io_operations.load()
         << ",\"io_failures\":" << io_failures.load()
         << ",\"downstream_requests\":" << downstream_requests.load()
         << ",\"downstream_failures\":" << downstream_failures.load()
         << ",\"downstream_mean_latency_ms\":"
         << (downstream_requests.load() == 0
                 ? 0.0
                 : static_cast<double>(downstream_latency_ns.load()) /
                       static_cast<double>(downstream_requests.load()) /
                       1'000'000.0)
         << ",\"rss_mb\":" << rss_megabytes()
         << ",\"cpu_auto_stop_remaining_seconds\":"
         << cpu_fault.remaining_seconds()
         << ",\"lock_auto_stop_remaining_seconds\":"
         << lock_fault.remaining_seconds()
         << ",\"memory_auto_stop_remaining_seconds\":"
         << memory_fault.remaining_seconds()
         << ",\"io_auto_stop_remaining_seconds\":"
         << io_fault.remaining_seconds()
         << ",\"downstream_auto_stop_remaining_seconds\":"
         << downstream_fault.remaining_seconds() << "}";
  return output.str();
}

std::string application_metrics_json() {
  const auto captured_at_ms =
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count();
  const auto requests = downstream_requests.load(std::memory_order_relaxed);
  const auto latency_ns =
      downstream_latency_ns.load(std::memory_order_relaxed);
  std::ostringstream output;
  output << std::fixed << std::setprecision(2)
         << "{\"schema_version\":\"mini-drop.application-metrics.v1\""
         << ",\"runtime\":\"c++\""
         << ",\"captured_at_unix_ms\":" << captured_at_ms
         << ",\"pid\":" << getpid()
         << ",\"host_pid\":" << host_pid()
         << ",\"cpu_operations\":"
         << cpu_operations.load(std::memory_order_relaxed)
         << ",\"lock_acquisitions\":"
         << lock_acquisitions.load(std::memory_order_relaxed)
         << ",\"lock_wait_ms\":"
         << static_cast<double>(lock_wait_ns.load(std::memory_order_relaxed)) /
                1'000'000.0
         << ",\"retained_memory_mb\":" << retained_memory_mb()
         << ",\"io_bytes_written\":"
         << io_bytes_written.load(std::memory_order_relaxed)
         << ",\"io_operations\":"
         << io_operations.load(std::memory_order_relaxed)
         << ",\"io_failures\":"
         << io_failures.load(std::memory_order_relaxed)
         << ",\"downstream_requests\":" << requests
         << ",\"downstream_failures\":"
         << downstream_failures.load(std::memory_order_relaxed)
         << ",\"downstream_mean_latency_ms\":"
         << (requests == 0
                 ? 0.0
                 : static_cast<double>(latency_ns) /
                       static_cast<double>(requests) / 1'000'000.0)
         << ",\"rss_mb\":" << rss_megabytes() << "}";
  return output.str();
}

void application_metrics_publisher() {
  const std::string path = kApplicationMetricsPath;
  const std::string temporary = path + ".tmp";
  while (true) {
    {
      std::ofstream output(temporary, std::ios::trunc);
      output << application_metrics_json();
      output.close();
      if (output) {
        std::rename(temporary.c_str(), path.c_str());
      }
    }
    std::this_thread::sleep_for(200ms);
  }
}

void send_json(int client, int status, const std::string& body) {
  const char* reason = status == 200 ? "OK" : "Not Found";
  std::ostringstream response;
  response << "HTTP/1.1 " << status << ' ' << reason << "\r\n"
           << "Content-Type: application/json\r\n"
           << "Content-Length: " << body.size() << "\r\n"
           << "Connection: close\r\n\r\n"
           << body;
  const std::string encoded = response.str();
  std::size_t sent = 0;
  while (sent < encoded.size()) {
    const auto count =
        ::send(client, encoded.data() + sent, encoded.size() - sent, 0);
    if (count <= 0) {
      break;
    }
    sent += static_cast<std::size_t>(count);
  }
}

void handle_client(int client) {
  std::string request;
  request.reserve(8192);
  char buffer[2048];
  while (request.size() < 8192) {
    const auto count = ::recv(client, buffer, sizeof(buffer), 0);
    if (count <= 0) {
      break;
    }
    request.append(buffer, static_cast<std::size_t>(count));
    const auto headers_end = request.find("\r\n\r\n");
    if (headers_end != std::string::npos) {
      const auto length_header = request.find("Content-Length:");
      std::size_t expected = 0;
      if (length_header != std::string::npos) {
        try {
          expected = static_cast<std::size_t>(
              std::stoul(request.substr(length_header + 15)));
        } catch (...) {
          expected = 0;
        }
      }
      if (request.size() >= headers_end + 4 + expected) {
        break;
      }
    }
  }

  const auto first_space = request.find(' ');
  const auto second_space = request.find(' ', first_space + 1);
  const std::string method =
      first_space == std::string::npos ? "" : request.substr(0, first_space);
  const std::string path =
      second_space == std::string::npos
          ? ""
          : request.substr(first_space + 1, second_space - first_space - 1);
  const auto body_start = request.find("\r\n\r\n");
  const std::string body = body_start == std::string::npos
                               ? ""
                               : request.substr(body_start + 4);

  if (method == "GET" && (path == "/health" || path == "/snapshot")) {
    send_json(client, 200, snapshot_json());
  } else if (method == "GET" && path == "/work") {
    if (downstream_fault.active()) {
      std::this_thread::sleep_for(std::chrono::milliseconds(
          downstream_delay_ms.load(std::memory_order_relaxed)));
    }
    send_json(client, 200, "{\"ok\":true}");
  } else if (method == "POST" && path == "/faults/cpu/start") {
    cpu_operations.store(0);
    cpu_fault.start(json_integer(body, "duration_seconds", 60));
    send_json(client, 200, snapshot_json());
  } else if (method == "POST" && path == "/faults/cpu/stop") {
    cpu_fault.stop();
    send_json(client, 200, snapshot_json());
  } else if (method == "POST" && path == "/faults/lock/start") {
    lock_acquisitions.store(0);
    lock_wait_ns.store(0);
    lock_fault.start(json_integer(body, "duration_seconds", 60));
    send_json(client, 200, snapshot_json());
  } else if (method == "POST" && path == "/faults/lock/stop") {
    lock_fault.stop();
    send_json(client, 200, snapshot_json());
  } else if (method == "POST" && path == "/faults/memory/start") {
    memory_target_mb.store(std::clamp(json_integer(body, "megabytes", 96),
                                      kMinMemoryMegabytes,
                                      kMaxMemoryMegabytes));
    release_retained_memory();
    memory_fault.start(json_integer(body, "duration_seconds", 60));
    send_json(client, 200, snapshot_json());
  } else if (method == "POST" && path == "/faults/memory/stop") {
    memory_fault.stop();
    release_retained_memory();
    send_json(client, 200, snapshot_json());
  } else if (method == "POST" && path == "/faults/io/start") {
    io_bytes_written.store(0);
    io_operations.store(0);
    io_failures.store(0);
    cleanup_io_fault_file();
    io_fault.start(json_integer(body, "duration_seconds", 60));
    send_json(client, 200, snapshot_json());
  } else if (method == "POST" && path == "/faults/io/stop") {
    io_fault.stop();
    cleanup_io_fault_file();
    send_json(client, 200, snapshot_json());
  } else if (method == "POST" && path == "/faults/downstream/start") {
    downstream_requests.store(0);
    downstream_failures.store(0);
    downstream_latency_ns.store(0);
    downstream_delay_ms.store(std::clamp(
        json_integer(body, "delay_ms", 260),
        kMinDownstreamDelayMilliseconds, kMaxDownstreamDelayMilliseconds));
    downstream_fault.start(json_integer(body, "duration_seconds", 60));
    send_json(client, 200, snapshot_json());
  } else if (method == "POST" && path == "/faults/downstream/stop") {
    downstream_fault.stop();
    send_json(client, 200, snapshot_json());
  } else {
    send_json(client, 404, "{\"error\":\"route not allow-listed\"}");
  }
  close(client);
}

}  // namespace

int main() {
  std::signal(SIGPIPE, SIG_IGN);
  std::thread(cpu_worker).detach();
  std::thread(lock_owner).detach();
  for (int index = 0; index < 4; ++index) {
    std::thread(lock_waiter).detach();
  }
  std::thread(memory_worker).detach();
  std::thread(io_worker).detach();
  for (int index = 0; index < 2; ++index) {
    std::thread(downstream_worker).detach();
  }
  std::thread(application_metrics_publisher).detach();

  const int server = socket(AF_INET, SOCK_STREAM, 0);
  if (server < 0) {
    std::cerr << "failed to create socket\n";
    return 1;
  }
  int reuse = 1;
  setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_ANY);
  address.sin_port = htons(kPort);
  if (bind(server, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0 ||
      listen(server, 32) < 0) {
    std::cerr << "failed to listen on port " << kPort << '\n';
    close(server);
    return 1;
  }
  std::cout << "cpp fault lab listening on :" << kPort << std::endl;
  while (true) {
    const int client = accept(server, nullptr, nullptr);
    if (client >= 0) {
      std::thread(handle_client, client).detach();
    }
  }
}
