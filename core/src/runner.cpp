#include "drop/runner.hpp"
#include "drop/bolt.hpp"
#include "drop/artifact.hpp"
#include "drop/allocator_probe.hpp"
#include "drop/domain.hpp"
#include "drop/json.hpp"
#include "drop/sampling.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstring>
#include <curl/curl.h>
#include <deque>
#include <fcntl.h>
#include <fstream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <sys/klog.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

namespace fs = std::filesystem;
using json = nlohmann::json;
using namespace std::chrono_literals;
namespace dropcore {
namespace {
json kernel_logs() {
  json result = {
      {"status", "unavailable"},
      {"reason", "Kernel log collection is disabled by Agent policy"},
      {"lines", json::array()},
      {"truncated", false}};
  if (secret("ALLOW_KERNEL_LOGS", "false") != "true")
    return result;
  const auto size = klogctl(10, nullptr, 0);
  if (size < 0) {
    result["status"] = (errno == EPERM || errno == EACCES) ? "permission_denied"
                                                           : "unavailable";
    result["reason"] = "Kernel did not permit a nonconsuming log snapshot";
    return result;
  }
  std::string text(static_cast<std::size_t>(std::min(size, 65536)), '\0');
  const auto count = klogctl(3, text.data(), static_cast<int>(text.size()));
  if (count < 0) {
    result["status"] = (errno == EPERM || errno == EACCES) ? "permission_denied"
                                                           : "unavailable";
    result["reason"] =
        "Kernel log snapshot is unavailable under current permissions";
    return result;
  }
  text.resize(static_cast<std::size_t>(count));
  return bounded_kernel_snapshot(text, size > 65536);
}
double cpu_seconds(const timeval &time) {
  return static_cast<double>(time.tv_sec) +
         static_cast<double>(time.tv_usec) / 1000000.0;
}
json overhead(const rusage &self_start, const rusage &children_start) {
  rusage self{}, children{};
  getrusage(RUSAGE_SELF, &self);
  getrusage(RUSAGE_CHILDREN, &children);
  return {
      {"user_cpu_seconds", std::max(0.0, cpu_seconds(self.ru_utime) -
                                             cpu_seconds(self_start.ru_utime))},
      {"system_cpu_seconds",
       std::max(0.0,
                cpu_seconds(self.ru_stime) - cpu_seconds(self_start.ru_stime))},
      {"children_user_cpu_seconds",
       std::max(0.0, cpu_seconds(children.ru_utime) -
                         cpu_seconds(children_start.ru_utime))},
      {"children_system_cpu_seconds",
       std::max(0.0, cpu_seconds(children.ru_stime) -
                         cpu_seconds(children_start.ru_stime))},
      {"scope", "collector process CPU (including concurrent heartbeat "
                "threads), plus reaped child profiler tools; interval deltas"}};
}
void network_sample(int pid, json &sample) {
  try {
    const auto path =
        pid ? "/proc/" + std::to_string(pid) + "/net/dev" : "/proc/net/dev";
    std::istringstream input(read_file(path, 65536));
    std::string line;
    std::uint64_t received = 0, sent = 0;
    while (std::getline(input, line)) {
      const auto colon = line.find(':');
      if (colon == std::string::npos)
        continue;
      std::istringstream fields(line.substr(colon + 1));
      std::uint64_t value = 0;
      for (int index = 0; index < 16 && fields >> value; ++index) {
        if (index == 0)
          received += value;
        if (index == 8)
          sent += value;
      }
    }
    sample["network_rx_bytes"] = received;
    sample["network_tx_bytes"] = sent;
  } catch (const std::exception &) {
    sample["network_available"] = false;
  }
}
ThreadCpuSnapshot cpu_threads(int pid) {
  ThreadCpuSnapshot result;
  for (const auto &entry :
       fs::directory_iterator("/proc/" + std::to_string(pid) + "/task")) {
    try {
      const auto stat = read_file((entry.path() / "stat").string(), 8192);
      const auto left = stat.find('('), right = stat.rfind(')');
      if (left == std::string::npos || right == std::string::npos)
        continue;
      std::istringstream fields(stat.substr(right + 2));
      std::string value;
      std::uint64_t user = 0, system = 0;
      for (int i = 0; i <= 12 && fields >> value; ++i) {
        if (i == 11)
          user = std::stoull(value);
        if (i == 12)
          system = std::stoull(value);
      }
      result.emplace(std::stoi(entry.path().filename().string()),
                     ThreadCpuSample{stat.substr(left + 1, right - left - 1),
                                     user + system});
    } catch (const std::exception &) {
    }
  }
  return result;
}
std::uint64_t caps() {
  std::istringstream in(read_file("/proc/self/status", 65536));
  std::string line;
  while (std::getline(in, line))
    if (line.rfind("CapEff:", 0) == 0)
      return std::stoull(line.substr(7), nullptr, 16);
  return 0;
}
bool capability(unsigned int bit) { return (caps() & (1ULL << bit)) != 0; }
void pause(const RunnerContext &c) {
  const auto end = std::chrono::steady_clock::now() +
                   std::chrono::seconds(c.duration_seconds);
  while (std::chrono::steady_clock::now() < end) {
    if (c.canceled())
      throw std::runtime_error("TASK_CANCELED");
    if (c.target.pid &&
        !same_identity(c.target, process_identity(c.target.pid)))
      throw std::runtime_error("TARGET_CHANGED");
    std::this_thread::sleep_for(50ms);
  }
}
std::string output_name(const RunnerContext &c) {
  if (c.kind == "JAVA_CPU")
    return "java.folded";
  if (c.kind == "PYTHON_CPU")
    return "python.folded";
  if (c.kind == "PYTHON_MEMORY")
    return "allocations.bin";
  if (c.kind == "JAVA_HEAP")
    return "heap.hprof";
  if (c.kind == "IO_TRACE")
    return "io.jsonl";
  if (c.kind == "MEMORY_TRACE")
    return "memory.jsonl";
  if (c.kind == "BOLT_PROFILE")
    return "profile.fdata";
  return "native.profile";
}
void check_result(const ProcessResult &result, bool trace = false) {
  if (result.process_limit_exceeded)
    throw ProfilerExecutionFailure(result.exit_code, "RUNNER_PROCESS_LIMIT");
  if (result.canceled)
    throw std::runtime_error("TASK_CANCELED");
  if (result.timed_out)
    throw std::runtime_error("RUNNER_TIMEOUT");
  if (result.exit_code != 0)
    throw ProfilerExecutionFailure(result.exit_code,
                                   trace ? classify_trace_failure(result.output)
                                         : "RUNNER_FAILED");
}
void native_control(const RunnerContext &c, const std::string &operation,
                    const fs::path &output) {
  const auto deadline = std::chrono::steady_clock::now() + 5s;
  const auto root = fs::canonical(
      secret("NATIVE_CONTROL_ROOT",
             c.output_directory.empty()
                 ? "/run/drop-profilers"
                 : (fs::path(secret("COLLECTOR_HELPER_INCOMING_DIR",
                                    "/run/drop-helper-incoming")) /
                    "native-control")
                       .string()));
  std::uint64_t scratch_bound = 0;
  if (!c.output_directory.empty()) {
    scratch_bound =
        std::stoull(secret("COLLECTOR_HELPER_DISK_BYTES", "2147483648"));
    struct stat control {
    }, output{};
    if (stat(root.c_str(), &control) ||
        stat(c.output_directory.c_str(), &output) ||
        !S_ISDIR(control.st_mode) || control.st_uid != c.target.uid ||
        (control.st_mode & 0077) || control.st_dev != output.st_dev ||
        fs::space(root).capacity > scratch_bound)
      throw std::runtime_error("Native profiler scratch must share the "
                               "protected bounded RAW filesystem");
  }
  // The cooperative application names its socket using getpid() in its own
  // namespace. SO_PEERCRED below still verifies the controller-visible PID.
  const auto helper_pid =
      c.target.namespace_pid > 0 ? c.target.namespace_pid : c.target.pid;
  const auto socket_path = root / (std::to_string(helper_pid) + ".sock");
  struct stat info {};
  if (lstat(socket_path.c_str(), &info) != 0 || !S_ISSOCK(info.st_mode) ||
      info.st_uid != c.target.uid)
    throw std::runtime_error(
        "Native profiler control socket is missing or has the wrong owner");
  int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (fd < 0)
    throw std::runtime_error("Native control socket failed");
  struct Guard {
    int fd;
    ~Guard() { close(fd); }
  } guard{fd};
  timeval timeout{5, 0};
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  const auto path = socket_path.string();
  if (path.size() >= sizeof(address.sun_path))
    throw std::runtime_error("Control root path too long");
  std::copy(path.begin(), path.end(), address.sun_path);
  if (connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0)
    throw std::runtime_error(
        "Native profiler target did not accept control connection");
  struct ucred peer {};
  socklen_t length = sizeof(peer);
  if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &length) != 0 ||
      peer.pid != c.target.pid || peer.uid != c.target.uid)
    throw std::runtime_error(
        "Native profiler peer identity does not match selected target");
  const auto request = json{{"schema_version", 2},
                            {"operation", operation},
                            {"scratch_filesystem_max_bytes", scratch_bound},
                            {"mode", c.kind == "GPERF_CPU"    ? "cpu"
                                     : c.kind == "GPERF_HEAP" ? "heap"
                                                              : "jemalloc"}}
                           .dump() +
                       "\n";
  if (send(fd, request.data(), request.size(), MSG_NOSIGNAL) !=
      static_cast<ssize_t>(request.size()))
    throw std::runtime_error("Native profiler command failed");
  std::string header;
  char ch = 0;
  while (header.size() < 128 && recv(fd, &ch, 1, 0) == 1 && ch != '\n')
    header += ch;
  if (header.rfind("OK2 ", 0) != 0)
    throw std::runtime_error("Native target rejected profiling operation");
  auto size = std::stoull(header.substr(4));
  if (size > c.max_file_bytes)
    throw std::runtime_error("Native profile exceeds task budget");
  Guard file{-1};
  if (!output.empty()) {
    file.fd =
        open(output.c_str(),
             O_WRONLY | O_CREAT | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC, 0600);
    struct stat status {};
    if (file.fd < 0 || fstat(file.fd, &status) || !S_ISREG(status.st_mode) ||
        status.st_uid != geteuid() || status.st_nlink != 1 ||
        ftruncate(file.fd, 0))
      throw std::runtime_error(
          "Native RAW must be a private owned regular file");
  }
  std::array<char, 65536> bytes{};
  std::uint64_t received = 0;
  while (received < size) {
    if (std::chrono::steady_clock::now() > deadline)
      throw std::runtime_error("Native profile transfer deadline exceeded");
    if (c.canceled() && operation != "stop")
      throw std::runtime_error("TASK_CANCELED");
    auto count =
        recv(fd, bytes.data(),
             std::min<std::uint64_t>(bytes.size(), size - received), 0);
    if (count <= 0)
      throw std::runtime_error("Truncated native profile response");
    if (file.fd >= 0) {
      std::size_t written = 0;
      while (written < static_cast<std::size_t>(count)) {
        const auto size = write(file.fd, bytes.data() + written,
                                static_cast<std::size_t>(count) - written);
        if (size < 0 && errno == EINTR)
          continue;
        if (size <= 0)
          throw std::runtime_error("Native profile write failed");
        written += static_cast<std::size_t>(size);
      }
    }
    received += static_cast<std::uint64_t>(count);
  }
  if (file.fd >= 0 && fsync(file.fd))
    throw std::runtime_error("Native profile sync failed");
}
std::string allocator_library(const RunnerContext &c) {
  std::istringstream maps(read_file(
      "/proc/" + std::to_string(c.target.pid) + "/maps", 1024 * 1024));
  std::string line, library;
  while (std::getline(maps, line)) {
    const auto slash = line.find('/');
    if (slash != std::string::npos &&
        (line.find("libc.so") != std::string::npos ||
         line.find("libjemalloc") != std::string::npos)) {
      library = "/proc/" + std::to_string(c.target.pid) + "/root" +
                line.substr(slash);
      std::istringstream fields(line);
      std::string range, permissions, offset, device;
      std::uint64_t inode = 0;
      struct stat file {};
      if (!(fields >> range >> permissions >> offset >> device >> inode) ||
          stat(library.c_str(), &file) || !S_ISREG(file.st_mode))
        throw std::runtime_error("Mapped allocator library unavailable");
      const auto colon = device.find(':');
      if (colon == std::string::npos || colon == 0 ||
          colon + 1 == device.size() ||
          device.find_first_not_of("0123456789abcdefABCDEF:") !=
              std::string::npos ||
          device.find(':', colon + 1) != std::string::npos ||
          std::stoull(device.substr(0, colon), nullptr, 16) !=
              major(file.st_dev) ||
          std::stoull(device.substr(colon + 1), nullptr, 16) !=
              minor(file.st_dev) ||
          inode != file.st_ino)
        throw std::runtime_error("Mapped allocator library identity changed");
      if (line.find("libjemalloc") != std::string::npos)
        break;
    }
  }
  if (library.empty() ||
      library.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRST"
                                "UVWXYZ0123456789/_-.+") != std::string::npos)
    throw std::invalid_argument(
        "Cannot resolve a safe target allocator library");
  return library;
}
std::string trace_program(const RunnerContext &c) {
  std::string program;
  if (c.kind == "IO_TRACE") {
    program =
        R"BPF(tracepoint:syscalls:sys_enter_read,tracepoint:syscalls:sys_enter_write /pid == DROP_PID/ { @start[tid]=nsecs; @op[tid]=probe; }
tracepoint:syscalls:sys_exit_read,tracepoint:syscalls:sys_exit_write /pid == DROP_PID && @start[tid]/ { $d=nsecs-@start[tid]; if($d >= DROP_MINIMUM) { printf("{\"schema_version\":1,\"event\":\"io\",\"time_ns\":%llu,\"pid\":%d,\"tid\":%d,\"bytes\":%ld,\"duration_ns\":%llu,\"operation\":\"%s\"}\n",nsecs,pid,tid,args->ret,$d,@op[tid]); @events=@events+1; } delete(@start[tid]); delete(@op[tid]); }
)BPF";
    const auto category = c.options.value("category", "syscalls");
    if (category == "vfs")
      program =
          R"BPF(kprobe:vfs_read,kprobe:vfs_write /pid == DROP_PID/ { $f=(struct file*)arg0; @start[tid]=nsecs; @op[tid]=probe; @inode[tid]=$f->f_inode->i_ino; @dev[tid]=$f->f_inode->i_sb->s_dev; }
kretprobe:vfs_read,kretprobe:vfs_write /pid == DROP_PID && @start[tid]/ { $d=nsecs-@start[tid]; if($d >= DROP_MINIMUM) { printf("{\"schema_version\":1,\"event\":\"io\",\"time_ns\":%llu,\"pid\":%d,\"tid\":%d,\"bytes\":%ld,\"duration_ns\":%llu,\"inode\":%llu,\"device\":%llu,\"operation\":\"%s\"}\n",nsecs,pid,tid,retval,$d,@inode[tid],@dev[tid],@op[tid]); @events=@events+1; } delete(@start[tid]); delete(@op[tid]); delete(@inode[tid]); delete(@dev[tid]); }
)BPF";
    else if (category == "block")
      program =
          R"BPF(kprobe:submit_bio_noacct /pid == DROP_PID/ { $b=(struct bio*)arg0; $key=(uint64)arg0; @begin[$key]=nsecs; @owner[$key]=pid; @thread[$key]=tid; @bytes[$key]=$b->bi_iter.bi_size; @sector[$key]=$b->bi_iter.bi_sector; @major[$key]=$b->bi_bdev->bd_dev >> 20; @minor[$key]=$b->bi_bdev->bd_dev & 1048575; @stack[$key]=ustack(perf,DROP_DEPTH); }
kprobe:bio_endio /@begin[(uint64)arg0]/ { $key=(uint64)arg0; $d=nsecs-@begin[$key]; if($d >= DROP_MINIMUM) { printf("{\"schema_version\":1,\"event\":\"io\",\"time_ns\":%llu,\"pid\":%d,\"tid\":%d,\"bytes\":%llu,\"duration_ns\":%llu,\"device\":\"%d:%d\",\"operation\":\"block\",\"sector\":%llu}\n",nsecs,@owner[$key],@thread[$key],@bytes[$key],$d,@major[$key],@minor[$key],@sector[$key]); @events=@events+1; } delete(@begin[$key]); delete(@owner[$key]); delete(@thread[$key]); delete(@bytes[$key]); delete(@sector[$key]); delete(@major[$key]); delete(@minor[$key]); delete(@stack[$key]); }
)BPF";
    else if (category != "syscalls")
      throw std::invalid_argument("I/O category not allowlisted");
  } else {
    const auto category = c.options.value("category", "pagefault");
    if (category == "alloc") {
      const auto library = allocator_library(c);
      program =
          R"BPF(uprobe:DROP_LIBRARY:ma*lloc /pid == DROP_PID/ { @size[tid]=arg0; }
uretprobe:DROP_LIBRARY:ma*lloc /pid == DROP_PID/ { if(retval != 0) { printf("{\"schema_version\":1,\"event\":\"alloc\",\"time_ns\":%llu,\"pid\":%d,\"tid\":%d,\"address\":%llu,\"size\":%llu}\n",nsecs,pid,tid,retval,@size[tid]); @events=@events+1; } delete(@size[tid]); }
uprobe:DROP_LIBRARY:fr*ee /pid == DROP_PID/ { printf("{\"schema_version\":1,\"event\":\"free\",\"time_ns\":%llu,\"pid\":%d,\"tid\":%d,\"address\":%llu}\n",nsecs,pid,tid,arg0); }
)BPF";
      for (std::size_t at = 0;
           (at = program.find("DROP_LIBRARY", at)) != std::string::npos;
           at += library.size())
        program.replace(at, 12, library);
    } else if (category == "mmap")
      program =
          R"BPF(tracepoint:syscalls:sys_enter_mmap /pid == DROP_PID/ { @len[tid]=args->len; }
tracepoint:syscalls:sys_exit_mmap /pid == DROP_PID && @len[tid]/ { if(args->ret >= 0) { printf("{\"schema_version\":1,\"event\":\"mmap\",\"time_ns\":%llu,\"pid\":%d,\"tid\":%d,\"address\":%llu,\"size\":%llu}\n",nsecs,pid,tid,args->ret,@len[tid]); @events=@events+1; } delete(@len[tid]); }
tracepoint:syscalls:sys_enter_munmap /pid == DROP_PID/ { @unmap_addr[tid]=args->addr; @unmap_len[tid]=args->len; }
tracepoint:syscalls:sys_exit_munmap /pid == DROP_PID && @unmap_len[tid]/ { if(args->ret == 0) { printf("{\"schema_version\":1,\"event\":\"munmap\",\"time_ns\":%llu,\"pid\":%d,\"tid\":%d,\"address\":%llu,\"size\":%llu}\n",nsecs,pid,tid,@unmap_addr[tid],@unmap_len[tid]); @events=@events+1; } delete(@unmap_addr[tid]); delete(@unmap_len[tid]); }
)BPF";
    else
      program =
          R"BPF(software:page-faults:1 /pid == DROP_PID/ { printf("{\"schema_version\":1,\"event\":\"pagefault\",\"time_ns\":%llu,\"pid\":%d,\"tid\":%d}\n",nsecs,pid,tid); @events=@events+1; }
)BPF";
  }
  // Enforce the event budget before writing each event, including frees.
  // A periodic exit check alone permits unbounded output between ticks.
  const std::string increment = " @events=@events+1;";
  for (std::size_t at = 0;
       (at = program.find(increment, at)) != std::string::npos;)
    program.erase(at, increment.size());
  for (std::size_t at = 0;
       (at = program.find("printf(", at)) != std::string::npos;) {
    const auto end = program.find(");", at);
    if (end == std::string::npos)
      throw std::logic_error("Invalid fixed trace event");
    const auto statement = program.substr(at, end + 2 - at);
    const auto bounded =
        "if(@events < DROP_LIMIT) { " + statement + " @events=@events+1; }";
    program.replace(at, statement.size(), bounded);
    at += bounded.size();
  }
  // A single printf record carries both JSON and its actual user stack. The
  // bounded decoder below converts stack lines to a JSON array; raw bpftrace
  // multiline output is never mislabeled as valid JSONL.
  const std::string marker = "}\\n\",";
  for (std::size_t at = 0; (at = program.find(marker, at)) != std::string::npos;
       at += 50)
    program.replace(at, marker.size(),
                    "}\\nDROP_STACK_BEGIN\\n%s\\nDROP_STACK_END\\n\",");
  for (std::size_t at = 0;
       (at = program.find("printf(", at)) != std::string::npos;) {
    const auto end = program.find(");", at);
    if (end == std::string::npos)
      throw std::logic_error("Invalid fixed trace template");
    const auto stack =
        c.kind == "IO_TRACE" &&
                c.options.value("category", "syscalls") == "block"
            ? ",@stack[$key]"
            : ",ustack(perf,DROP_DEPTH)";
    program.insert(end, stack);
    at = end + std::strlen(stack) + 2;
  }
  // Stop producers before draining asynchronous user-stack records. Some
  // bpftrace builds keep multi-uprobe links active during shutdown; without a
  // persistent gate, new records can prevent the drain from ever completing.
  for (std::size_t at = 0;
       (at = program.find(" /", at)) != std::string::npos;) {
    const auto end = program.find("/ {", at + 2);
    if (end == std::string::npos || program.find('\n', at) < end)
      throw std::logic_error("Invalid fixed trace predicate");
    program.insert(end, ")");
    program.insert(at + 2, "!@drop_done && (");
    at = end + 19;
  }
  program +=
      R"BPF(interval:s:1 /@events >= DROP_LIMIT/ { @drop_done=1; exit(); } interval:s:DROP_DURATION { @drop_done=1; exit(); } END { @drop_done=1; clear(@events); )BPF";
  if (c.kind == "IO_TRACE") {
    const auto category = c.options.value("category", "syscalls");
    if (category == "block")
      program += "clear(@begin); clear(@owner); clear(@thread); clear(@bytes); "
                 "clear(@sector); clear(@major); clear(@minor); clear(@stack);";
    else {
      program += "clear(@start); clear(@op);";
      if (category == "vfs")
        program += "clear(@inode); clear(@dev);";
    }
  } else if (c.options.value("category", "pagefault") == "alloc")
    program += "clear(@size);";
  else if (c.options.value("category", "pagefault") == "mmap")
    program += "clear(@len); clear(@unmap_addr); clear(@unmap_len);";
  program += " }";
  if (!c.target.pid_namespace.empty()) {
    const auto inode =
        c.target.pid_namespace.substr(5, c.target.pid_namespace.size() - 6);
    if (inode.empty() ||
        inode.find_first_not_of("0123456789") != std::string::npos)
      throw std::invalid_argument("Invalid pinned PID namespace identity");
    const std::string
        leader = "((struct task_struct*)curtask)->group_leader->thread_pid",
        thread = "((struct task_struct*)curtask)->thread_pid";
    // bpftrace 0.20 only permits literal array subscripts. The BTF upid
    // offset retains the live namespace level without guessing host depth.
    const auto upid = [](const std::string &identity) {
      return "((struct upid*)((uint64)" + identity +
             " + offsetof(struct pid,numbers) + (uint64)" + identity +
             "->level * sizeof(struct upid)))";
    };
    const auto number = upid(leader), thread_number = upid(thread);
    const std::map<std::string, std::string> local{
        {"pid == DROP_PID", "((struct pid_namespace*)" + number +
                                "->ns)->ns.inum == " + inode + " && " + number +
                                "->nr == DROP_NAMESPACE_PID"},
        {"nsecs,pid,tid,", "nsecs,DROP_PID," + thread_number + "->nr,"},
        {"nsecs,pid,tid)", "nsecs,DROP_PID," + thread_number + "->nr)"},
        {"@owner[$key]=pid; @thread[$key]=tid;",
         "@owner[$key]=DROP_PID; @thread[$key]=" + thread_number + "->nr;"}};
    for (const auto &[key, value] : local)
      for (std::size_t at = 0;
           (at = program.find(key, at)) != std::string::npos;
           at += value.size())
        program.replace(at, key.size(), value);
  }
  const std::map<std::string, std::string> replacements{
      {"DROP_PID", std::to_string(c.target.pid)},
      {"DROP_NAMESPACE_PID",
       std::to_string(c.target.namespace_pid > 0 ? c.target.namespace_pid
                                                 : c.target.pid)},
      {"DROP_MINIMUM",
       std::to_string(c.options.value("min_duration_us", 0LL) * 1000)},
      {"DROP_LIMIT", std::to_string(c.options.value("max_events", 10000))},
      {"DROP_DEPTH", std::to_string(c.options.value("stack_depth", 32))},
      {"DROP_DURATION", std::to_string(c.duration_seconds)}};
  for (const auto &[key, value] : replacements)
    for (std::size_t at = 0; (at = program.find(key, at)) != std::string::npos;
         at += value.size())
      program.replace(at, key.size(), value);
  return program;
}
} // namespace

std::vector<std::string>
build_runner_argv(const RunnerContext &c,
                  const std::map<std::string, std::string> &tools) {
  if (c.target.pid <= 1 && c.kind != "RESOURCE_SNAPSHOT")
    throw std::invalid_argument("Unsafe runner PID");
  if (c.duration_seconds < 1 || c.duration_seconds > 60 || c.frequency_hz < 1 ||
      c.frequency_hz > 199)
    throw std::invalid_argument("Runner budget invalid");
  auto tool = [&](const std::string &id) {
    auto f = tools.find(id);
    if (f == tools.end() || f->second.empty() || f->second.front() != '/')
      throw std::runtime_error("Required tool unavailable: " + id);
    return f->second;
  };
  const auto output = ((c.output_directory.empty() ? c.directory
                                                   : c.output_directory) /
                       output_name(c))
                          .string(),
             pid = std::to_string(c.target.pid);
  if (c.kind == "JAVA_CPU") {
    const auto event = c.options.value("event", "cpu");
    if (event != "cpu" && event != "alloc" && event != "lock")
      throw std::invalid_argument("Java profiler event not allowlisted");
    return {tool("asprof"),
            "-e",
            event == "cpu" && c.java_cpu_timer ? "ctimer" : event,
            "-d",
            std::to_string(c.duration_seconds),
            "-j",
            "127",
            "-o",
            "collapsed",
            "-f",
            output,
            pid};
  }
  if (c.kind == "PYTHON_CPU") {
    std::vector<std::string> result{
        tool("py-spy"), "record",
        "--format",     "raw",
        "--output",     output,
        "--pid",        pid,
        "--duration",   std::to_string(c.duration_seconds),
        "--rate",       std::to_string(c.frequency_hz)};
    if (c.options.value("native", false))
      result.push_back("--native");
    return result;
  }
  if (c.kind == "PYTHON_MEMORY") {
    std::vector<std::string> result{
        tool("memray"), "attach",
        "--method",     "gdb",
        "--output",     output,
        "--duration",   std::to_string(c.duration_seconds)};
    if (c.options.value("native", false))
      result.push_back("--native");
    result.push_back(pid);
    return result;
  }
  if (c.kind == "JAVA_HEAP") {
    // Native jattach resolves the target mount namespace even when the PID
    // namespace is shared. JDK17 jcmd assumes a shared /tmp in that topology.
    // The diagnostic command has its own tokenizer: only an owned simple path
    // is accepted, and no caller can add another diagnostic argument/command.
    if (output.empty() || output.front() != '/' || output.size() > 4096 ||
        output.find_first_not_of(
            "/._-"
            "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ") !=
            std::string::npos)
      throw std::invalid_argument("Heap output path is not command-safe");
    return {tool("asprof"), "jcmd", pid,
            "GC.heap_dump " +
                std::string(c.options.value("live", true) ? "" : "-all ") +
                output};
  }
  if (c.kind == "IO_TRACE" || c.kind == "MEMORY_TRACE") {
    if (c.kind == "MEMORY_TRACE" &&
        c.options.value("category", "pagefault") == "alloc")
      return {tool("bpftrace"), "-q", "-p", pid, "-e", trace_program(c)};
    return {tool("bpftrace"), "-q", "-e", trace_program(c)};
  }
  if (c.kind == "SCRIPT") {
    const auto id = c.options.value("template_id", "process_status");
    if (id != "process_status" && id != "process_limits" &&
        id != "open_files" && id != "mapped_libraries" &&
        id != "network_counters")
      throw std::invalid_argument("Unknown script template");
    return {};
  }
  if (c.kind == "BOLT_PROFILE")
    return {tool("perf2bolt"),
            "/proc/" + pid + "/exe",
            "-p",
            (c.directory / "bolt.perf.data").string(),
            "-o",
            output,
            "-nl",
            "-aggregate-only"};
  if (c.kind == "RESOURCE_SNAPSHOT" || c.kind == "GO_CPU" ||
      c.kind == "GO_HEAP" || c.kind == "GPERF_CPU" || c.kind == "GPERF_HEAP" ||
      c.kind == "JEPROF_HEAP")
    return {};
  throw std::invalid_argument("Unsupported external runner");
}

RunnerRegistry::RunnerRegistry(const Catalog &catalog, std::string scratch,
                               std::string perf)
    : catalog_(catalog) {
  for (const auto &[kind, spec] : catalog.entries()) {
    if (spec.value("agent_runner", true) == false ||
        spec.value("runner", "") == "import")
      continue;
    std::string reason;
    json versions = json::array();
    for (const auto &requirement :
         spec.value("tool_requirements", json::array())) {
      const auto name = requirement.at("name").get<std::string>();
      if (!tools_.contains(name)) {
        ToolInfo info;
        info.name = name;
        info.path =
            name == "perf"
                ? perf
                : secret(requirement.at("executable_env")
                             .get<std::string>()
                             .c_str(),
                         requirement.at("default_path").get<std::string>());
        try {
          // Keep argv[0] aliases: LLVM perf2bolt is a multicall symlink whose
          // execution mode depends on the configured name. Hash the resolved
          // file, but execute the absolute configured alias.
          info.path = fs::absolute(info.path).lexically_normal().string();
          const auto resolved = fs::canonical(info.path);
          for (auto parent = resolved.parent_path(); !parent.empty();
               parent = parent.parent_path()) {
            struct stat directory {};
            if (::stat(parent.c_str(), &directory) != 0 ||
                (directory.st_mode & 0022) ||
                (directory.st_uid != 0 && directory.st_uid != getuid()))
              throw std::runtime_error("Tool resolved parent directory is "
                                       "writable by an untrusted principal");
            if (parent == parent.root_path())
              break;
          }
          for (auto parent = fs::path(info.path).parent_path(); !parent.empty();
               parent = parent.parent_path()) {
            struct stat directory {};
            if (::stat(parent.c_str(), &directory) != 0 ||
                (directory.st_mode & 0022) ||
                (directory.st_uid != 0 && directory.st_uid != getuid()))
              throw std::runtime_error("Tool parent directory is writable by "
                                       "an untrusted principal");
            if (parent == parent.root_path())
              break;
          }
          struct stat stat {};
          if (::stat(info.path.c_str(), &stat) != 0 || !S_ISREG(stat.st_mode) ||
              (stat.st_mode & 0022) || access(info.path.c_str(), X_OK) != 0)
            throw std::runtime_error(
                "Tool is missing, nonregular, nonexecutable or writable by an "
                "untrusted principal");
          info.sha256 = sha256_file(info.path);
          auto expected =
              secret(requirement.at("sha256_env").get<std::string>().c_str());
          const auto manifest =
              secret("TOOL_MANIFEST_FILE", "/opt/drop-tools/manifest.json");
          if (fs::is_regular_file(manifest)) {
            const auto pinned = parse_json(read_file(manifest, 1024 * 1024));
            if (pinned.at("tools").contains(name)) {
              const auto &entry = pinned.at("tools").at(name);
              if (expected.empty())
                expected = entry.at("sha256").get<std::string>();
              info.support_files = entry.value(
                  "support_files", std::map<std::string, std::string>{});
            }
          }
          if (expected.empty())
            throw std::runtime_error(
                "No trusted tool hash configured; set SHA256 or install the "
                "trusted build tool manifest");
          for (const auto &[path, hash] : info.support_files)
            if (sha256_file(path) != hash)
              throw std::runtime_error("Tool support library SHA256 mismatch");
          if (name == "gdb") {
            const std::string launcher = "/usr/local/bin/drop_gdb_ptrace";
            const std::string alias = "/opt/drop-tools/gdb-bin/gdb";
            if (!info.support_files.contains(launcher) ||
                !info.support_files.contains(alias) ||
                fs::canonical(alias) != fs::canonical(launcher) ||
                parse_json(read_file(manifest, 1024 * 1024))
                        .at("tools")
                        .at("gdb")
                        .at("path") != info.path)
              throw std::runtime_error("GDB requires its pinned ptrace-only "
                                       "launcher and matching manifest path");
          }
          if (!expected.empty() && expected != info.sha256)
            throw std::runtime_error(
                "Tool SHA256 differs from configured allowlist");
          std::vector<std::string> argv{info.path};
          for (const auto &argument : requirement.at("probe_argv"))
            argv.push_back(argument.get<std::string>());
          // LLVM/JVM initialization can exceed five seconds on an I/O-limited
          // node even for --version. Keep a hard bound without disabling a
          // healthy profiler merely because its shared libraries are cold.
          const auto probe_timeout =
              name == "bpftrace" || name == "jcmd" || name == "memray" ? 15s
                                                                       : 5s;
          auto probe =
              execute(argv, scratch + "/tool-" + name + ".log", probe_timeout);
          if (probe.exit_code != 0)
            throw std::runtime_error(probe.timed_out
                                         ? "Tool version probe timed out"
                                         : "Tool version probe failed");
          info.version = probe.output.substr(0, 1024);
          info.available = true;
        } catch (const std::exception &error) {
          info.reason = error.what();
        }
        tools_.emplace(name, std::move(info));
      }
      const auto &info = tools_.at(name);
      versions.push_back({{"name", name},
                          {"path", info.path},
                          {"sha256", info.sha256},
                          {"version", info.version},
                          {"available", info.available},
                          {"reason", info.reason}});
      if (!info.available)
        reason = "Required tool unavailable: " + name;
    }
    if ((kind == "PYTHON_CPU" || kind == "PYTHON_MEMORY") && !capability(19)) {
      try {
        if (std::stoi(read_file("/proc/sys/kernel/yama/ptrace_scope", 32)) != 0)
          reason = "SYS_PTRACE or permissive ptrace policy is required for "
                   "attaching";
      } catch (const std::exception &) {
        reason = "Cannot verify ptrace permission";
      }
    }
    if ((kind == "IO_TRACE" || kind == "MEMORY_TRACE") &&
        (!capability(39) || !capability(38)))
      reason = "CAP_BPF and CAP_PERFMON are required by this trace runner";
    capabilities_[kind] = {{"available", reason.empty()},
                           {"reason", reason},
                           {"tools", versions},
                           {"target_probe_required", true},
                           {"runner", spec.at("runner")}};
    if (kind == "IO_TRACE" || kind == "MEMORY_TRACE") {
      auto &categories = capabilities_[kind]["category_capabilities"];
      categories = json::object();
      for (const auto &category :
           kind == "IO_TRACE"
               ? std::vector<std::string>{"syscalls", "vfs", "block"}
               : std::vector<std::string>{"alloc", "mmap", "pagefault"})
        categories[category] = {
            {"available", reason.empty() ? json(nullptr) : json(false)},
            {"status",
             reason.empty() ? "target_probe_required" : "unavailable"},
            {"reason",
             reason.empty()
                 ? "Kernel attachment must be verified for the selected target"
                 : reason}};
    }
    runners_[kind] = [this] {
      return make_managed_runner(
          [this](const RunnerContext &c) {
            catalog_.validate(c.kind, c.parameters);
            if (c.target.pid &&
                !same_identity(c.target, process_identity(c.target.pid)))
              throw std::runtime_error("TARGET_CHANGED");
            if (!available(c.kind))
              throw std::runtime_error(
                  "Required profiler capability is not available");
            // GDB must stop newly created target threads. Ptrace permission
            // alone does not grant cross-UID signal permission. Signal zero
            // checks permission without stopping or otherwise mutating a task.
            if (c.kind == "PYTHON_MEMORY" && c.target.pid > 1 &&
                c.target.uid != geteuid() && ::kill(c.target.pid, 0) != 0)
              throw ProfilerExecutionFailure(1, "PROFILER_PERMISSION_DENIED");
          },
          [this](const RunnerContext &c) {
            if (!fs::is_directory(c.directory) || fs::is_symlink(c.directory))
              throw std::invalid_argument(
                  "Runner workspace is not a private directory");
            if (!c.output_directory.empty() &&
                (!fs::is_directory(c.output_directory) ||
                 fs::is_symlink(c.output_directory)))
              throw std::invalid_argument(
                  "Cooperative output directory is invalid");
            for (const auto &requirement : catalog_.get(c.kind).value(
                     "tool_requirements", json::array())) {
              const auto &t = tool(requirement.at("name").get<std::string>());
              if (sha256_file(t.path) != t.sha256)
                throw std::runtime_error("TOOL_INTEGRITY_CHANGED");
              for (const auto &[path, hash] : t.support_files)
                if (sha256_file(path) != hash)
                  throw std::runtime_error("TOOL_SUPPORT_INTEGRITY_CHANGED");
            }
          },
          [this](const RunnerContext &c) { return collect_impl(c); });
    };
  }
}
bool RunnerRegistry::available(const std::string &kind) const {
  auto f = capabilities_.find(kind);
  return f != capabilities_.end() && f->second.value("available", false);
}
const ToolInfo &RunnerRegistry::tool(const std::string &name) const {
  return tools_.at(name);
}
json RunnerRegistry::capabilities() const {
  json value = json::object();
  for (const auto &[kind, status] : capabilities_)
    value[kind] = status;
  return value;
}
std::vector<std::string> RunnerRegistry::argv(const RunnerContext &c) const {
  std::map<std::string, std::string> paths;
  for (const auto &[name, tool] : tools_)
    paths[name] = tool.path;
  return build_runner_argv(c, paths);
}

namespace {
struct HttpOutput {
  std::ofstream file;
  std::uint64_t size = 0, limit = 0;
};
std::size_t save_http(char *data, std::size_t size, std::size_t count,
                      void *ptr) {
  auto &out = *static_cast<HttpOutput *>(ptr);
  const auto bytes = size * count;
  if (bytes > out.limit - out.size)
    return 0;
  out.file.write(data, static_cast<std::streamsize>(bytes));
  out.size += bytes;
  return out.file ? bytes : 0;
}
int cancel_http(void *value, curl_off_t, curl_off_t, curl_off_t, curl_off_t) {
  return static_cast<const RunnerContext *>(value)->canceled() ? 1 : 0;
}
bool process_owns_port(int pid, int port) {
  std::set<std::string> sockets;
  for (const auto &entry :
       fs::directory_iterator("/proc/" + std::to_string(pid) + "/fd")) {
    try {
      const auto link = fs::read_symlink(entry.path()).string();
      if (link.rfind("socket:[", 0) == 0)
        sockets.insert(link.substr(8, link.size() - 9));
    } catch (const std::exception &) {
    }
  }
  for (const auto *table : {"tcp", "tcp6"}) {
    std::istringstream input(read_file(
        "/proc/" + std::to_string(pid) + "/net/" + table, 1024 * 1024));
    std::string line;
    std::getline(input, line);
    while (std::getline(input, line)) {
      std::istringstream row(line);
      std::vector<std::string> fields;
      std::string value;
      while (row >> value)
        fields.push_back(value);
      if (fields.size() < 10 || fields[3] != "0A")
        continue;
      const auto colon = fields[1].rfind(':');
      if (colon != std::string::npos &&
          std::stoi(fields[1].substr(colon + 1), nullptr, 16) == port &&
          sockets.contains(fields[9]))
        return true;
    }
  }
  return false;
}
} // namespace
void download_pprof(const RunnerContext &c, const fs::path &output) {
  const auto port = c.options.value("port", 6060);
  if (port < 1 || port > 65535 || !process_owns_port(c.target.pid, port))
    throw std::invalid_argument(
        "pprof listener must belong to the selected process");
  std::string host = "127.0.0.1";
  if (fs::read_symlink("/proc/" + std::to_string(c.target.pid) + "/ns/net") !=
      fs::read_symlink("/proc/self/ns/net")) {
    std::istringstream table(
        read_file("/proc/" + std::to_string(c.target.pid) + "/net/fib_trie",
                  1024 * 1024));
    std::string line, previous;
    while (std::getline(table, line)) {
      if (line.find("/32 host LOCAL") != std::string::npos) {
        std::istringstream candidate(previous);
        std::string value, last;
        while (candidate >> value)
          last = value;
        if (last.rfind("127.", 0) != 0 &&
            last.find_first_not_of("0123456789.") == std::string::npos)
          host = last;
      }
      previous = line;
    }
    if (host == "127.0.0.1")
      throw std::runtime_error("Container pprof listener network address is "
                               "not reachable from Agent");
  }
  const auto path =
      c.kind == "GO_CPU" ? "/debug/pprof/profile" : "/debug/pprof/heap";
  if (c.options.value("path", path) != path)
    throw std::invalid_argument("pprof HTTP path is not allowlisted");
  const auto url =
      "http://" + host + ":" + std::to_string(port) + path +
      (c.kind == "GO_CPU" ? "?seconds=" + std::to_string(c.duration_seconds)
                          : (c.options.value("gc", false) ? "?gc=1" : ""));
  HttpOutput out{std::ofstream(output, std::ios::binary), 0, c.max_file_bytes};
  if (!out.file)
    throw std::runtime_error("Cannot open pprof output");
  std::unique_ptr<CURL, decltype(&curl_easy_cleanup)> curl(curl_easy_init(),
                                                           curl_easy_cleanup);
  if (!curl)
    throw std::runtime_error("HTTP initialization failed");
  curl_easy_setopt(curl.get(), CURLOPT_URL, url.c_str());
  curl_easy_setopt(curl.get(), CURLOPT_PROXY, "");
  curl_easy_setopt(curl.get(), CURLOPT_PROTOCOLS_STR, "http");
  curl_easy_setopt(curl.get(), CURLOPT_CONNECTTIMEOUT, 3L);
  curl_easy_setopt(curl.get(), CURLOPT_TIMEOUT,
                   static_cast<long>(c.duration_seconds + 10));
  curl_easy_setopt(curl.get(), CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(curl.get(), CURLOPT_WRITEFUNCTION, save_http);
  curl_easy_setopt(curl.get(), CURLOPT_WRITEDATA, &out);
  curl_easy_setopt(curl.get(), CURLOPT_XFERINFOFUNCTION, cancel_http);
  curl_easy_setopt(curl.get(), CURLOPT_XFERINFODATA, &c);
  curl_easy_setopt(curl.get(), CURLOPT_NOPROGRESS, 0L);
  long status = 0;
  const auto result = curl_easy_perform(curl.get());
  curl_easy_getinfo(curl.get(), CURLINFO_RESPONSE_CODE, &status);
  if (result != CURLE_OK || status != 200 || out.size == 0)
    throw std::runtime_error("pprof HTTP capture failed or exceeded budget");
}

std::string classify_trace_failure(const std::string &diagnostic) {
  const auto bounded = diagnostic.substr(0, 16384);
  if (bounded.find("Permission denied") != std::string::npos ||
      bounded.find("Operation not permitted") != std::string::npos ||
      bounded.find("EACCES") != std::string::npos ||
      bounded.find("EPERM") != std::string::npos)
    return "PROFILER_PERMISSION_DENIED";
  if (bounded.find("Read-only file system") != std::string::npos ||
      bounded.find("No probes to attach") != std::string::npos ||
      bounded.find("not traceable") != std::string::npos ||
      bounded.find("tracepoint not found") != std::string::npos)
    return "TRACE_ATTACH_UNAVAILABLE";
  return "RUNNER_FAILED";
}
void wait_for_cooperative_output_close(const RunnerContext &context,
                                       std::chrono::milliseconds timeout) {
  const auto directory = context.output_directory.empty()
                             ? context.directory
                             : context.output_directory;
  const auto deadline = std::chrono::steady_clock::now() + timeout;
  for (;;) {
    if (context.canceled())
      throw std::runtime_error("TASK_CANCELED");
    if (!same_identity(context.target, process_identity(context.target.pid)))
      throw std::runtime_error("TARGET_CHANGED");
    std::set<std::pair<std::uint64_t, std::uint64_t>> outputs;
    std::size_t count = 0;
    for (const auto &entry : fs::directory_iterator(directory)) {
      struct stat file {};
      if (++count > 64 || lstat(entry.path().c_str(), &file) ||
          !S_ISREG(file.st_mode) || file.st_nlink != 1)
        throw std::runtime_error(
            "Invalid cooperative output before completion");
      outputs.emplace(file.st_dev, file.st_ino);
    }
    bool writing = false;
    count = 0;
    for (const auto &entry : fs::directory_iterator(
             "/proc/" + std::to_string(context.target.pid) + "/fd")) {
      struct stat file {};
      if (++count > 4096)
        throw std::runtime_error(
            "Cooperative output descriptor budget exceeded");
      if (stat(entry.path().c_str(), &file)) {
        if (errno == ENOENT)
          continue;
        throw std::runtime_error(
            "Cooperative output descriptor inspection failed");
      }
      if (S_ISREG(file.st_mode) && outputs.contains({file.st_dev, file.st_ino}))
        writing = true;
    }
    if (!writing)
      return;
    if (std::chrono::steady_clock::now() >= deadline)
      throw std::runtime_error("RUNNER_TIMEOUT");
    std::this_thread::sleep_for(50ms);
  }
}
std::string classify_bolt_failure(const std::string &diagnostic) {
  if (diagnostic.size() > 16384)
    return "BOLT_AGGREGATION_FAILED";
  if (diagnostic.find("failed to match build-id from perf output") !=
      std::string::npos)
    return "BOLT_BINARY_SAMPLES_UNAVAILABLE";
  if (diagnostic.find("PERF-ERROR: return code") != std::string::npos)
    return "BOLT_PERF_CONVERSION_FAILED";
  return "BOLT_AGGREGATION_FAILED";
}

bool exact_allocator_probe_listing(const std::string &listing,
                                   const std::string &library,
                                   const std::string &symbol) {
  if (listing.size() > 65536 || (symbol != "malloc" && symbol != "free"))
    return false;
  std::istringstream lines(listing);
  std::string line;
  unsigned matches = 0;
  const auto expected = "uprobe:" + library + ":" + symbol;
  while (std::getline(lines, line)) {
    const auto first = line.find_first_not_of(" \t\r");
    if (first == std::string::npos)
      continue;
    line = line.substr(first, line.find_last_not_of(" \t\r") - first + 1);
    if (!line.starts_with("uprobe:") && !line.starts_with("uretprobe:"))
      continue;
    if (line != expected || ++matches != 1)
      return false;
  }
  return matches == 1;
}

json bounded_kernel_snapshot(const std::string &text, bool truncated) {
  std::deque<std::string> lines;
  std::istringstream input(text);
  std::string line;
  std::size_t bytes = 0;
  while (std::getline(input, line)) {
    if (line.size() > 1024) {
      line.resize(1024);
      truncated = true;
    }
    line = parse_json(
               json(line).dump(-1, ' ', false, json::error_handler_t::replace),
               8192)
               .get<std::string>();
    while (!lines.empty() &&
           (lines.size() >= 200 || bytes + line.size() > 60000)) {
      bytes -= lines.front().size();
      lines.pop_front();
      truncated = true;
    }
    bytes += line.size();
    lines.push_back(line);
  }
  return {{"status", "ok"}, {"lines", lines}, {"truncated", truncated}};
}
json resource_sample(int pid) {
  const auto timestamp =
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count();
  json sample = {{"timestamp_ns", timestamp}};
  network_sample(pid, sample);
  if (!pid) {
    std::istringstream memory(read_file("/proc/meminfo", 65536));
    std::string key, unit;
    std::uint64_t value, total = 0, available = 0;
    while (memory >> key >> value >> unit) {
      if (key == "MemTotal:")
        total = value * 1024;
      if (key == "MemAvailable:")
        available = value * 1024;
    }
    sample["memory_total_bytes"] = total;
    sample["memory_used_bytes"] = total - available;
    sample["load_average"] = read_file("/proc/loadavg", 1024);
    std::istringstream switches(read_file("/proc/stat", 65536));
    std::string item;
    while (std::getline(switches, item))
      if (item.starts_with("ctxt "))
        sample["context_switches"] = std::stoull(item.substr(5));
    std::istringstream cpu(read_file("/proc/stat", 65536));
    std::string label;
    cpu >> label;
    std::uint64_t count = 0, total_ticks = 0, idle_ticks = 0;
    for (int index = 0; index < 8; ++index) {
      cpu >> count;
      total_ticks += count;
      if (index == 3 || index == 4)
        idle_ticks += count;
    }
    sample["cpu_total_ticks"] = total_ticks;
    sample["cpu_idle_ticks"] = idle_ticks;
    return sample;
  }
  std::istringstream stat(
      read_file("/proc/" + std::to_string(pid) + "/stat", 8192));
  std::string text((std::istreambuf_iterator<char>(stat)), {});
  std::istringstream fields(text.substr(text.rfind(')') + 2));
  std::vector<std::string> values;
  std::string value;
  while (fields >> value)
    values.push_back(value);
  if (values.size() < 22)
    throw std::runtime_error("Incomplete process resource stat");
  sample["cpu_ticks"] = std::stoull(values[11]) + std::stoull(values[12]);
  sample["threads"] = std::stoull(values[17]);
  sample["vms_bytes"] = std::stoull(values[20]);
  sample["rss_bytes"] = std::stoll(values[21]) * sysconf(_SC_PAGESIZE);
  sample["pid"] = pid;
  std::istringstream status(
      read_file("/proc/" + std::to_string(pid) + "/status", 65536));
  std::string item;
  while (std::getline(status, item)) {
    if (item.starts_with("voluntary_ctxt_switches:"))
      sample["voluntary_context_switches"] =
          std::stoull(item.substr(item.find(':') + 1));
    if (item.starts_with("nonvoluntary_ctxt_switches:"))
      sample["involuntary_context_switches"] =
          std::stoull(item.substr(item.find(':') + 1));
  }
  std::size_t descriptors = 0;
  for (const auto &entry :
       fs::directory_iterator("/proc/" + std::to_string(pid) + "/fd")) {
    (void)entry;
    ++descriptors;
  }
  sample["fd_count"] = descriptors;
  try {
    std::istringstream io(
        read_file("/proc/" + std::to_string(pid) + "/io", 8192));
    std::uint64_t number;
    while (io >> value >> number)
      if (value == "read_bytes:" || value == "write_bytes:")
        sample[value.substr(0, value.size() - 1)] = number;
  } catch (const std::exception &) {
    sample["io_available"] = false;
  }
  return sample;
}

RunnerOutput RunnerRegistry::collect(const RunnerContext &c) const {
  auto found = runners_.find(c.kind);
  if (found == runners_.end())
    throw std::invalid_argument("No registered Agent runner");
  auto session = found->second();
  try {
    session->Validate(c);
    session->Prepare(c);
    session->Start(c);
    while (!session->Poll())
      std::this_thread::sleep_for(20ms);
    auto result = session->Collect();
    session->Cleanup();
    return result;
  } catch (...) {
    session->Stop();
    session->Cleanup();
    throw;
  }
}
RunnerOutput RunnerRegistry::collect_impl(const RunnerContext &c) const {
  rusage self_start{}, children_start{};
  getrusage(RUSAGE_SELF, &self_start);
  getrusage(RUSAGE_CHILDREN, &children_start);
  if (!available(c.kind))
    throw std::runtime_error("Required profiler capability is not available");
  for (const auto &requirement :
       catalog_.get(c.kind).value("tool_requirements", json::array())) {
    const auto &t = tool(requirement.at("name").get<std::string>());
    if (sha256_file(t.path) != t.sha256)
      throw std::runtime_error("TOOL_INTEGRITY_CHANGED");
  }
  const auto &spec = catalog_.get(c.kind);
  RunnerOutput out{
      (c.output_directory.empty() ? c.directory : c.output_directory) /
          spec.at("raw_name").get<std::string>(),
      spec.at("raw_name"),
      spec.at("input_format"),
      spec.at("raw_content_type"),
      {{"schema_version", 1},
       {"runner", spec.at("runner")},
       {"kind", c.kind},
       {"target_pid", c.target.pid},
       {"target_namespace_pid", c.target.namespace_pid},
       {"target_pid_namespace", c.target.pid_namespace},
       {"start_time_ticks", c.target.start_time_ticks},
       {"tool_probes", capabilities_.at(c.kind).at("tools")}}};
  const auto started = std::chrono::steady_clock::now();
  if (c.kind == "JAVA_CPU") {
    const auto event = c.options.value("event", "cpu");
    out.metadata["requested_event"] = event;
    out.metadata["sampling_engine"] = event == "cpu" && c.java_cpu_timer
                                          ? "async_profiler_ctimer"
                                          : "async_profiler_auto";
    if (event == "cpu" && c.java_cpu_timer)
      out.metadata["limitation"] = "Same-UID CPU-clock timer profiling; kernel "
                                   "perf-event stacks are not captured";
  }
  if (c.kind == "JAVA_HEAP")
    out.metadata["heap_attach_backend"] = "async_profiler_native_jattach_dcmd";
  if (c.kind == "PERF_CPU") {
    SamplingParameters parameters{c.target.pid, c.duration_seconds,
                                  c.frequency_hz, "process"};
    auto command = perf_record_argv(
        parameters, (c.directory / "perf.data").string(), c.target.pid);
    command[0] = tool("perf").path;
    const auto event = c.options.value("event", "cpu-clock"),
               graph = c.options.value("call_graph", "fp");
    if (event != "cpu-clock" && event != "cycles" && event != "instructions")
      throw std::invalid_argument("Perf event not allowlisted");
    command[3] = event;
    if (graph == "dwarf") {
      command.erase(command.begin() + 6);
      command.insert(command.begin() + 6, {"--call-graph", "dwarf,8192"});
    } else if (graph != "fp")
      throw std::invalid_argument("Perf callgraph not allowlisted");
    command.insert(command.begin() + 2, {"--clockid", "mono"});
    check_result(execute(command, (c.directory / "runner.log").string(),
                         std::chrono::seconds(c.duration_seconds + 5),
                         c.canceled, c.limits));
    check_result(execute(
        {tool("perf").path, "script", "--ns", "--show-lost-events", "-F",
         "comm,pid,tid,time,event,ip,sym,dso", "-i",
         (c.directory / "perf.data").string()},
        (c.directory / "perf.script").string(), 20s, c.canceled, c.limits));
    const auto loss = scan_perf_loss(c.directory / "perf.data");
    if (c.protocol_version == 1) {
      out.name = "profile.folded";
      out.format = "folded";
      out.path = c.directory / out.name;
      fold_perf_script(c.directory / "perf.script", out.path);
    } else {
      fs::copy_file(c.directory / "perf.script", out.path,
                    fs::copy_options::overwrite_existing);
    }
    out.metadata["perf"] = {
        {"sample_count", loss.complete ? json(loss.samples) : json(nullptr)},
        {"lost_records",
         loss.complete ? json(loss.lost_records) : json(nullptr)},
        {"hardware_lost_samples",
         loss.complete ? json(loss.hardware_lost_samples) : json(nullptr)},
        {"lost_samples", loss.complete && loss.lost_records == 0
                             ? json(loss.hardware_lost_samples)
                             : json(nullptr)},
        {"loss_proof",
         loss.complete ? "complete_native_perf_record_scan" : "unknown"},
        {"loss_explanation", "PERF_RECORD_LOST counts missing records; their "
                             "sample fraction is unknown"},
        {"clock", "CLOCK_MONOTONIC"},
        {"timestamp_unit", "nanoseconds"},
        {"requested_frequency_hz", c.frequency_hz},
        {"native_data_sha256",
         sha256_file((c.directory / "perf.data").string())},
        {"fields", "comm,pid,tid,time,event,ip,sym,dso"},
        {"filters",
         {{"target_pid", c.target.pid},
          {"symbol_filter", nullptr},
          {"time_filter", nullptr},
          {"thread_filter", nullptr},
          {"explanation", "target process selected; no symbol, time or thread "
                          "post-filter applied by collector"}}}};
    out.metadata["event"] = event;
    out.metadata["call_graph"] = graph;
    out.metadata["sample_unit"] = "samples";
  } else if (c.kind == "PROC_CPU") {
    if (c.frequency_hz > 10) {
      throw std::invalid_argument("procfs frequency exceeds 10Hz");
    }
    auto before = cpu_threads(c.target.pid);
    ThreadCpuSnapshot totals;
    const auto end = started + std::chrono::seconds(c.duration_seconds);
    const auto interval = std::chrono::steady_clock::now() +
                          std::chrono::milliseconds(1000 / c.frequency_hz);
    auto next = interval;
    for (;;) {
      const auto at = std::min(next, end);
      while (std::chrono::steady_clock::now() < at) {
        if (c.canceled())
          throw std::runtime_error("TASK_CANCELED");
        std::this_thread::sleep_for(10ms);
      }
      if (!same_identity(c.target, process_identity(c.target.pid)))
        throw std::runtime_error("TARGET_CHANGED");
      auto after = cpu_threads(c.target.pid);
      accumulate_thread_cpu(before, after, totals);
      before = std::move(after);
      if (std::chrono::steady_clock::now() >= end)
        break;
      next += std::chrono::milliseconds(1000 / c.frequency_hz);
    }
    std::ofstream file(out.path);
    std::uint64_t count = 0;
    for (const auto &[tid, value] : totals) {
      auto name = value.name;
      for (auto &ch : name)
        if (ch == ';' || ch == '\n' || ch == '\r')
          ch = '_';
      file << "process:" << c.target.pid << " (procfs);thread:" << name << " ["
           << tid << "] " << value.ticks << '\n';
      count += value.ticks;
    }
    if (!count)
      throw std::runtime_error("NO_SAMPLES");
    out.metadata["sample_unit"] = "CPU ticks";
    out.metadata["limitation"] = "thread CPU accounting; no function stacks";
  } else if (c.kind == "GO_CPU" || c.kind == "GO_HEAP")
    download_pprof(c, out.path);
  else if (c.kind == "RESOURCE_SNAPSHOT") {
    json samples = json::array();
    const auto end = started + std::chrono::seconds(c.duration_seconds);
    std::uint64_t previous_ticks = 0, previous_idle = 0;
    std::int64_t previous_time = 0;
    do {
      if (c.canceled())
        throw std::runtime_error("TASK_CANCELED");
      auto value = resource_sample(c.target.pid);
      const auto collector = resource_sample(getpid());
      value["collector_cpu_ticks"] = collector.at("cpu_ticks");
      value["collector_rss_bytes"] = collector.at("rss_bytes");
      if (value.contains("cpu_ticks") && previous_time) {
        const auto ticks = value["cpu_ticks"].get<std::uint64_t>();
        const auto now = value["timestamp_ns"].get<std::int64_t>();
        value["cpu_percent"] = 100.0 *
                               static_cast<double>(ticks - previous_ticks) /
                               static_cast<double>(sysconf(_SC_CLK_TCK)) /
                               (static_cast<double>(now - previous_time) / 1e9);
      } else if (value.contains("cpu_total_ticks") && previous_time) {
        const auto total = value["cpu_total_ticks"].get<std::uint64_t>(),
                   idle = value["cpu_idle_ticks"].get<std::uint64_t>();
        if (total > previous_ticks)
          value["cpu_percent"] =
              100.0 * (1.0 - static_cast<double>(idle - previous_idle) /
                                 static_cast<double>(total - previous_ticks));
      }
      previous_ticks = value.value(
          "cpu_ticks", value.value("cpu_total_ticks", std::uint64_t{0}));
      previous_idle = value.value("cpu_idle_ticks", std::uint64_t{0});
      previous_time = value["timestamp_ns"];
      samples.push_back(std::move(value));
      std::this_thread::sleep_for(
          std::chrono::milliseconds(1000 / std::min(c.frequency_hz, 10)));
    } while (std::chrono::steady_clock::now() < end);
    atomic_write(
        out.path.string(),
        json{{"schema_version", 1},
             {"samples", samples},
             {"network_scope",
              c.target.pid
                  ? "target network namespace interface cumulative totals, not "
                    "process-exclusive traffic"
                  : "collector network namespace interface cumulative totals"},
             {"context_switch_scope",
              c.target.pid ? "target process main thread counters"
                           : "host-visible cumulative context switches"},
             {"collector_overhead", overhead(self_start, children_start)},
             {"dmesg", kernel_logs()},
             {"target", c.target.pid ? "process" : "host"}}
            .dump(-1, ' ', false, json::error_handler_t::replace));
  } else if (c.kind == "SCRIPT") {
    (void)argv(c);
    const auto id = c.options.value("template_id", "process_status");
    const auto base = "/proc/" + std::to_string(c.target.pid) + "/";
    std::string text;
    if (id == "open_files") {
      json files = json::array();
      for (const auto &entry : fs::directory_iterator(base + "fd")) {
        if (files.size() >= 1024)
          break;
        try {
          files.push_back(
              {{"fd", entry.path().filename().string()},
               {"target", fs::read_symlink(entry.path()).string()}});
        } catch (const std::exception &) {
        }
      }
      text = files.dump();
    } else
      text = read_file(base + (id == "process_limits"     ? "limits"
                               : id == "mapped_libraries" ? "maps"
                               : id == "network_counters" ? "net/dev"
                                                          : "status"),
                       256 * 1024);
    atomic_write(out.path.string(),
                 json{{"schema_version", 1},
                      {"template", id},
                      {"exit_code", 0},
                      {"stdout", text},
                      {"stderr", ""},
                      {"duration_ms",
                       std::chrono::duration_cast<std::chrono::milliseconds>(
                           std::chrono::steady_clock::now() - started)
                           .count()}}
                     .dump());
  } else if (c.kind == "GPERF_CPU" || c.kind == "GPERF_HEAP" ||
             c.kind == "JEPROF_HEAP") {
    native_control(c, "start", {});
    try {
      pause(c);
    } catch (...) {
      try {
        native_control(c, "stop", {});
      } catch (const std::exception &) {
      }
      throw;
    }
    native_control(c, "stop", out.path);
  } else if (c.kind == "BOLT_PROFILE") {
    // perf cannot always open target paths across mount namespaces. Preserve
    // the native capture and supplement only missing build-id metadata after
    // matching its MMAP2 PID/device/inode to the frozen real executable.
    const auto original = c.directory / "bolt.recorded.perf.data";
    const auto raw = c.directory / "bolt.perf.data";
    const auto executable = "/proc/" + std::to_string(c.target.pid) + "/exe";
    const auto binary_sha256 = sha256_file(executable);
    if (!same_identity(c.target, process_identity(c.target.pid)))
      throw std::runtime_error("TARGET_CHANGED");
    auto result = execute(
        {tool("perf").path, "record", "-e", "cycles:u", "-F",
         std::to_string(c.frequency_hz), "-p", std::to_string(c.target.pid),
         "-o", original.string(), "--", "/usr/bin/sleep",
         std::to_string(c.duration_seconds)},
        (c.directory / "runner.log").string(),
        std::chrono::seconds(c.duration_seconds + 10), c.canceled, c.limits);
    check_result(result);
    if (!same_identity(c.target, process_identity(c.target.pid)) ||
        sha256_file(executable) != binary_sha256)
      throw std::runtime_error("TARGET_CHANGED");
    if (!verify_bolt_perf_mapping(original, c.target, c.max_file_bytes))
      throw ProfilerExecutionFailure(1, "BOLT_TARGET_MAPPING_UNVERIFIED");
    result = execute({tool("perf").path, "buildid-list", "-i", executable},
                     (c.directory / "bolt-buildid.log").string(), 10s,
                     c.canceled, c.limits);
    check_result(result);
    const auto build_id = parse_bolt_build_id(result.output);
    if (build_id.empty())
      throw ProfilerExecutionFailure(1, "BOLT_TARGET_MAPPING_UNVERIFIED");
    const auto listing_path = c.directory / "bolt-recorded-buildids.log";
    result = execute({tool("perf").path, "buildid-list", "-i",
                      original.string()},
                     listing_path.string(), 10s, c.canceled, c.limits);
    check_result(result);
    if (fs::file_size(listing_path) > 16384)
      throw ProfilerExecutionFailure(1, "BOLT_TARGET_MAPPING_UNVERIFIED");
    const auto existing =
        match_bolt_build_ids(result.output, c.target.executable, build_id);
    if (existing == BoltBuildIdMatch::conflict)
      throw ProfilerExecutionFailure(1, "BOLT_TARGET_MAPPING_UNVERIFIED");
    const auto original_sha256 = sha256_file(original.string());
    if (existing == BoltBuildIdMatch::missing) {
      result = execute(
          {tool("perf").path, "inject", "--build-ids", "--known-build-ids",
           build_id + " " + c.target.executable, "-i", original.string(),
           "-o", raw.string()},
          (c.directory / "bolt-inject.log").string(), 20s, c.canceled,
          c.limits);
      check_result(result);
    } else {
      fs::copy_file(original, raw);
    }
    const auto verified_path = c.directory / "bolt-verified-buildids.log";
    result = execute({tool("perf").path, "buildid-list", "-i", raw.string()},
                     verified_path.string(), 10s, c.canceled, c.limits);
    check_result(result);
    if (fs::file_size(verified_path) > 16384 ||
        match_bolt_build_ids(result.output, c.target.executable, build_id) !=
            BoltBuildIdMatch::matched ||
        sha256_file(original.string()) != original_sha256 ||
        !same_identity(c.target, process_identity(c.target.pid)) ||
        sha256_file(executable) != binary_sha256)
      throw ProfilerExecutionFailure(1, "BOLT_TARGET_MAPPING_UNVERIFIED");
    auto nested_limits = c.limits;
    nested_limits.executable_path_prefix =
        fs::path(tool("perf").path).parent_path().string();
    result = execute(argv(c), (c.directory / "bolt.log").string(), 60s,
                     c.canceled, nested_limits);
    if (result.exit_code != 0 && !result.process_limit_exceeded &&
        !result.timed_out && !result.canceled)
      throw ProfilerExecutionFailure(result.exit_code,
                                     classify_bolt_failure(result.output));
    check_result(result);
    if (fs::file_size(out.path) <= 7 ||
        read_file(out.path.string(), 8).rfind("no_lbr", 0) != 0)
      throw std::runtime_error(
          "BOLT aggregator did not produce a textual no-LBR profile");
    out.metadata["binary_sha256"] = binary_sha256;
    out.metadata["target_build_id"] = build_id;
    out.metadata["recorded_perf_sha256"] = original_sha256;
    out.metadata["target_mmap_identity_verified"] = true;
    out.metadata["build_id_supplemented"] =
        existing == BoltBuildIdMatch::missing;
    out.metadata["sampling_event"] = "cycles:u";
    out.metadata["branch_history"] = "not_collected_no_lbr";
  } else {
    std::unique_ptr<AllocatorProbeAlias> allocator_alias;
    if (c.kind == "MEMORY_TRACE" &&
        c.options.value("category", "pagefault") == "alloc") {
      const auto library = allocator_library(c);
      for (const auto &[symbol, pattern] :
           std::array<std::pair<std::string, std::string>, 2>{
               {{"malloc", "ma*lloc"}, {"free", "fr*ee"}}}) {
        const auto listing = execute(
            {tool("bpftrace").path, "-l", "uprobe:" + library + ":" + pattern},
            (c.directory / "allocator-probe.log").string(), 5s, c.canceled,
            c.limits);
        check_result(listing, true);
        if (!exact_allocator_probe_listing(listing.output, library, symbol))
          throw ProfilerExecutionFailure(1, "TRACE_ATTACH_UNAVAILABLE");
      }
      out.metadata["allocator_symbols"] = {"malloc", "free"};
      out.metadata["allocator_attach_scope"] = "selected_pid_only";
      allocator_alias = std::make_unique<AllocatorProbeAlias>(c.target, library);
      out.metadata["allocator_library_sha256"] = sha256_file(library);
      out.metadata["allocator_probe_mode"] =
          "single_symbol_verified_globs; root_owned_same_inode_short_alias; "
          "legacy_single_uprobe_or_"
          "multi_uprobe_when_supported_by_bpftrace_and_kernel";
    }
    std::map<std::string, std::string> file_paths;
    if (c.kind == "IO_TRACE" &&
        c.options.value("category", "syscalls") == "vfs")
      for (const auto &fd : fs::directory_iterator(
               "/proc/" + std::to_string(c.target.pid) + "/fd")) {
        try {
          struct stat information {};
          if (::stat(fd.path().c_str(), &information) == 0) {
            const auto key = std::to_string(major(information.st_dev)) + ":" +
                             std::to_string(minor(information.st_dev)) + ":" +
                             std::to_string(information.st_ino);
            file_paths[key] = fs::read_symlink(fd.path()).string();
          }
        } catch (const std::exception &) {
        }
      }
    const auto capture = c.kind == "IO_TRACE" || c.kind == "MEMORY_TRACE"
                             ? c.directory / "trace.raw"
                             : c.directory / "runner.log";
    struct AttachmentCleanup {
      std::function<void()> action;
      ~AttachmentCleanup() {
        if (action)
          try {
            action();
          } catch (...) {
          }
      }
    } cleanup;
    auto execution_limits = c.limits;
    if (c.kind == "PYTHON_MEMORY") {
      (void)tool("gdb");
      execution_limits.executable_path_prefix = "/opt/drop-tools/gdb-bin";
      execution_limits.nested_tool_manifest_path =
          secret("TOOL_MANIFEST_FILE", "/opt/drop-tools/manifest.json");
      execution_limits.nested_tool_target_identity = json{
          {"pid", c.target.pid},
          {"uid", c.target.uid},
          {"start_time_ticks", c.target.start_time_ticks},
          {"pid_namespace", c.target.pid_namespace},
          {"namespace_pid", c.target.namespace_pid},
          {"executable_device", c.target.executable_device},
          {"executable_inode",
           c.target.executable_inode}}.dump();
      out.metadata["debugger_backend"] = "gdb_ptrace";
      out.metadata["debugger_proc_mem"] = "disabled by launcher-only seccomp; "
                                          "target and memray parent unchanged";
    }
    if (c.kind == "PYTHON_MEMORY")
      cleanup.action = [&] {
        if (!same_identity(c.target, process_identity(c.target.pid)))
          return;
        execute(
            {tool("memray").path, "detach", "--method", "gdb",
             std::to_string(c.target.pid)},
            (c.directory / "detach.log").string(), 5s, [] { return false; },
            execution_limits);
      };
    auto arguments = argv(c);
    if (allocator_alias) {
      auto &program = arguments.back();
      const auto &library = allocator_alias->library();
      const auto &alias = allocator_alias->target_path();
      std::size_t count = 0;
      for (std::size_t at = 0;
           (at = program.find(library, at)) != std::string::npos;
           at += alias.size()) {
        program.replace(at, library.size(), alias);
        ++count;
      }
      if (count != 3)
        throw std::logic_error("Allocator fixed probe template changed");
      allocator_alias->verify();
    }
    auto result =
        execute(arguments, capture.string(),
                std::chrono::seconds(
                    c.duration_seconds +
                    (c.kind == "MEMORY_TRACE" &&
                             c.options.value("category", "pagefault") == "alloc"
                         ? 60
                         : 30)),
                c.canceled, execution_limits);
    atomic_write((c.directory / "runner-result.json").string(),
                 json{{"exit_code", result.exit_code},
                      {"timed_out", result.timed_out},
                      {"canceled", result.canceled}}
                     .dump());
    if (result.exit_code != 0 && capture.filename() == "trace.raw") {
      // Keep fixed-tool diagnostics, never copied trace JSON or user stacks.
      std::ofstream diagnostic(c.directory / "runner.log");
      std::istringstream lines(result.output);
      std::string line;
      std::size_t retained = 0;
      while (std::getline(lines, line)) {
        if (!(line.starts_with("ERROR:") || line.starts_with("WARNING:") ||
              (line.starts_with("stdin:") &&
               line.find("ERROR") != std::string::npos)))
          continue;
        if (line.size() > 2048 || retained + line.size() + 1 > 8192)
          break;
        diagnostic << line << '\n';
        retained += line.size() + 1;
      }
    }
    check_result(result, c.kind == "IO_TRACE" || c.kind == "MEMORY_TRACE");
    if (allocator_alias)
      allocator_alias->verify();
    if (c.kind == "PYTHON_MEMORY") {
      pause(c);
      wait_for_cooperative_output_close(c);
      cleanup.action = {};
    }
    if (c.kind == "IO_TRACE" || c.kind == "MEMORY_TRACE") {
      std::ifstream input(capture);
      std::ofstream output(out.path);
      std::string line;
      std::size_t count = 0;
      const auto maximum =
          static_cast<std::size_t>(c.options.value("max_events", 10000));
      while (std::getline(input, line)) {
        if (line.empty() || line.front() != '{')
          continue;
        if (line.size() > 65536)
          throw std::runtime_error("Trace event exceeds budget");
        auto event = parse_json(line);
        if (!event.is_object() || event.value("schema_version", 0) != 1 ||
            event.value("pid", 0) != c.target.pid)
          throw std::runtime_error("Trace event identity/schema mismatch");
        if (event.contains("inode") && event.contains("device") &&
            event["device"].is_number_unsigned()) {
          const auto dev = event["device"].get<std::uint64_t>();
          const auto device =
              std::to_string(dev >> 20) + ":" + std::to_string(dev & 0xfffff);
          const auto key = device + ":" +
                           std::to_string(event["inode"].get<std::uint64_t>());
          event["device"] = device;
          if (file_paths.contains(key))
            event["path"] = file_paths.at(key);
        }
        if (!std::getline(input, line) || line != "DROP_STACK_BEGIN")
          throw std::runtime_error("Trace stack record is truncated");
        json stack = json::array();
        bool complete = false;
        while (std::getline(input, line)) {
          if (line == "DROP_STACK_END") {
            complete = true;
            break;
          }
          const auto first = line.find_first_not_of(" \t\r");
          if (first == std::string::npos)
            continue;
          if (line.size() > 2048 ||
              stack.size() >=
                  static_cast<std::size_t>(c.options.value("stack_depth", 32)))
            throw std::runtime_error("Trace stack exceeds depth budget");
          stack.push_back(line.substr(first));
        }
        if (!complete)
          throw std::runtime_error("Trace stack record is incomplete");
        event["stack"] = std::move(stack);
        if (++count > maximum)
          break;
        output << event.dump() << '\n';
      }
      if (!count)
        throw std::runtime_error("NO_TRACE_EVENTS");
      out.metadata["event_count"] = count;
      out.metadata["time_clock"] = "monotonic nanoseconds";
      out.metadata["stack_source"] = "bpftrace ustack(perf)";
      if (c.kind == "IO_TRACE" &&
          c.options.value("category", "syscalls") == "vfs")
        out.metadata["path_resolution"] =
            "device/inode matched to open target descriptors at collection "
            "start; files opened later may lack path";
    }
  }
  if (!fs::is_regular_file(out.path) || fs::is_symlink(out.path) ||
      fs::file_size(out.path) == 0 ||
      fs::file_size(out.path) > c.max_file_bytes)
    throw std::runtime_error(
        "Profiler output missing, empty or exceeds task budget");
  out.metadata["duration_ms"] =
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now() - started)
          .count();
  out.metadata["collector_overhead"] = overhead(self_start, children_start);
  return out;
}
} // namespace dropcore
