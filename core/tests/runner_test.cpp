#include "drop/runner.hpp"
#include <atomic>
#include <chrono>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
template <class F> void rejects(F &&f) {
  try {
    f();
  } catch (const std::exception &) {
    return;
  }
  throw std::runtime_error("Unsafe runner accepted");
}
int main() {
  using namespace dropcore;
  for (const auto &diagnostic : {"Permission denied: /private/profile",
                                 "perf_event_open: Operation not permitted",
                                 "attach EACCES", "BPF EPERM"}) {
    const auto code = classify_trace_failure(diagnostic);
    const ProfilerExecutionFailure failure(1, code);
    if (code != "PROFILER_PERMISSION_DENIED" ||
        std::string(failure.what()).find("private") != std::string::npos)
      throw std::runtime_error(
          "Permission refusal was ambiguous or disclosed diagnostics");
  }
  for (const auto &diagnostic :
       {"Read-only file system", "No probes to attach",
        "kernel symbol is not traceable", "tracepoint not found"})
    if (classify_trace_failure(diagnostic) != "TRACE_ATTACH_UNAVAILABLE")
      throw std::runtime_error("Unavailable trace attachment was not explicit");
  if (classify_trace_failure("unrelated syntax error") != "RUNNER_FAILED" ||
      classify_trace_failure(std::string(16384, 'x') + "EACCES") !=
          "RUNNER_FAILED")
    throw std::runtime_error(
        "Trace diagnostics exceeded bounds or invented a permission failure");
  {
    const auto directory = std::filesystem::temp_directory_path() /
                           ("drop-output-close-" + std::to_string(getpid()));
    if (!std::filesystem::create_directory(directory))
      throw std::runtime_error("Output completion fixture already exists");
    struct Cleanup {
      std::filesystem::path path;
      ~Cleanup() { std::filesystem::remove_all(path); }
    } cleanup{directory};
    const auto file = directory / "allocations.bin";
    const int writer =
        open(file.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (writer < 0)
      throw std::runtime_error("Cannot create completion writer fixture");
    std::atomic<bool> release_writer{false};
    std::jthread delayed_close(
        [writer, &release_writer](std::stop_token stopped) {
          while (!release_writer && !stopped.stop_requested())
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
          if (!stopped.stop_requested())
            std::this_thread::sleep_for(std::chrono::milliseconds(150));
          close(writer);
        });
    RunnerContext pending;
    pending.directory = directory;
    pending.target = process_identity(getpid());
    pending.canceled = [] { return false; };
    rejects([&] {
      wait_for_cooperative_output_close(pending, std::chrono::milliseconds(0));
    });
    const auto started = std::chrono::steady_clock::now();
    release_writer = true;
    wait_for_cooperative_output_close(pending);
    if (std::chrono::steady_clock::now() - started <
        std::chrono::milliseconds(90))
      throw std::runtime_error(
          "Cooperative completion accepted an open RAW writer");
    pending.canceled = [] { return true; };
    rejects([&] { wait_for_cooperative_output_close(pending); });
    pending.canceled = [] { return false; };
    const auto fifo = directory / "unexpected-fifo";
    if (mkfifo(fifo.c_str(), 0600))
      throw std::runtime_error("Cannot create output FIFO refusal fixture");
    rejects([&] { wait_for_cooperative_output_close(pending); });
  }
  RunnerContext c;
  const std::string library = "/proc/77/root/usr/lib/libc.so.6";
  if (!exact_allocator_probe_listing("uprobe:" + library + ":malloc\n", library,
                                     "malloc") ||
      exact_allocator_probe_listing("", library, "malloc") ||
      exact_allocator_probe_listing(
          "uprobe:" + library + ":malloc\nuprobe:" + library + ":mallocalias\n",
          library, "malloc") ||
      exact_allocator_probe_listing("uprobe:/other/libc.so.6:malloc\n", library,
                                    "malloc") ||
      exact_allocator_probe_listing(
          "uprobe:" + library + ":malloc\nuprobe:" + library + ":malloc\n",
          library, "malloc"))
    throw std::runtime_error("Allocator wildcard expansion did not require "
                             "exactly the intended target symbol");
  c.kind = "MEMORY_TRACE";
  c.target.pid = getpid();
  c.options = {{"category", "alloc"}};
  const auto allocator_arguments =
      build_runner_argv(c, {{"bpftrace", "/usr/bin/bpftrace"}});
  if (allocator_arguments.size() != 6 || allocator_arguments[2] != "-p" ||
      allocator_arguments[3] != std::to_string(c.target.pid) ||
      allocator_arguments[4] != "-e")
    throw std::runtime_error("Allocator uprobes must attach only to the "
                             "selected process, not the global libc inode");
  const auto &allocator = allocator_arguments.back();
  if (allocator.find(":ma*lloc ") == std::string::npos ||
      allocator.find(":fr*ee ") == std::string::npos)
    throw std::runtime_error("Allocator trace no longer permits the bounded "
                             "multi-uprobe attachment path");
  c.target.pid = 4321;
  c.options = nlohmann::json::object();
  c.directory = "/tmp/drop-safe-task";
  c.duration_seconds = 5;
  c.frequency_hz = 99;
  c.kind = "PYTHON_CPU";
  std::map<std::string, std::string> tools{{"py-spy", "/opt/tools/py-spy"},
                                           {"asprof", "/opt/tools/asprof"},
                                           {"jcmd", "/usr/bin/jcmd"}};
  tools["bpftrace"] = "/usr/bin/bpftrace";
  auto a = build_runner_argv(c, tools);
  if (a.front() != "/opt/tools/py-spy" || a.back() != "99")
    throw std::runtime_error("py-spy argv mismatch");
  c.kind = "PYTHON_MEMORY";
  tools["memray"] = "/opt/tools/memray";
  a = build_runner_argv(c, tools);
  const auto method = std::find(a.begin(), a.end(), "--method");
  if (method == a.end() || std::next(method) == a.end() ||
      *std::next(method) != "gdb")
    throw std::runtime_error(
        "Memray must use the pinned GDB ptrace launcher across target UIDs");
  c.kind = "JAVA_CPU";
  c.output_directory = "/bounded/shared/raw-only";
  c.options = nlohmann::json::object();
  auto shared = build_runner_argv(c, tools);
  if (std::find(shared.begin(), shared.end(),
                "/bounded/shared/raw-only/java.folded") == shared.end())
    throw std::runtime_error(
        "Cooperative RAW did not use independent receive directory");
  c.output_directory.clear();
  c.java_cpu_timer = true;
  auto timer = build_runner_argv(c, tools);
  if (std::find(timer.begin(), timer.end(), "ctimer") == timer.end())
    throw std::runtime_error("Unprivileged same-UID Java CPU must explicitly "
                             "report its CPU-clock timer engine");
  c.java_cpu_timer = false;
  c.options = {{"event", "cpu;touch /tmp/pwn"}};
  rejects([&] { build_runner_argv(c, tools); });
  c.kind = "JAVA_HEAP";
  c.options = nlohmann::json::object();
  a = build_runner_argv(c, tools);
  if (a.size() != 4 || a[0] != tools.at("asprof") || a[1] != "jcmd" ||
      a[2] != std::to_string(c.target.pid) ||
      !a[3].starts_with("GC.heap_dump ") ||
      a[3].find("-all") != std::string::npos)
    throw std::runtime_error(
        "Heap dump must use the pinned native jattach command");
  c.options = {{"live", false}};
  if (!build_runner_argv(c, tools).at(3).starts_with("GC.heap_dump -all "))
    throw std::runtime_error("All-object heap dump lost its explicit option");
  for (const auto &unsafe :
       {"/tmp/a b", "/tmp/a\nGC.run", "/tmp/\"a\"", "/tmp/a\\b"}) {
    c.output_directory = unsafe;
    rejects([&] { build_runner_argv(c, tools); });
  }
  c.output_directory.clear();
  c.kind = "SCRIPT";
  c.options = {{"template_id", "../../etc/shadow"}};
  rejects([&] { build_runner_argv(c, tools); });
  c.kind = "JAVA_CPU";
  c.options = {{"event", "alloc"}};
  a = build_runner_argv(c, tools);
  if (std::find(a.begin(), a.end(), "collapsed") == a.end())
    throw std::runtime_error("Java output must be collapsed");
  c.kind = "IO_TRACE";
  c.options = {{"category", "syscalls"}, {"stack_depth", 16}};
  a = build_runner_argv(c, tools);
  if (a.size() != 4 || a.back().find("pid == 4321") == std::string::npos ||
      a.back().find("ustack(perf,16)") == std::string::npos ||
      a.back().find("DROP_PID") != std::string::npos)
    throw std::runtime_error(
        "Trace program must bind safe PID and stack depth");
  if (a.back().find("/!@drop_done && (") == std::string::npos ||
      a.back().find("{ @drop_done=1; exit(); }") == std::string::npos ||
      a.back().find("END { @drop_done=1;") == std::string::npos ||
      a.back().find("clear(@drop_done)") != std::string::npos)
    throw std::runtime_error(
        "Trace producers must stop before shutdown drains stacks");
  c.options = {{"category", "block"}, {"stack_depth", 16}};
  a = build_runner_argv(c, tools);
  if (a.back().find("kprobe:submit_bio_noacct") == std::string::npos ||
      a.back().find("kprobe:bio_endio") == std::string::npos ||
      a.back().find("@owner[$key]=pid") == std::string::npos ||
      a.back().find("$b->bi_iter.bi_size") == std::string::npos ||
      a.back().find("blk_mq_start_request") != std::string::npos)
    throw std::runtime_error("Block I/O must correlate submitting PID with "
                             "asynchronous bio completion");
  c.kind = "MEMORY_TRACE";
  c.options = {{"category", "mmap"}};
  a = build_runner_argv(c, tools);
  if (a.back().find("sys_exit_mmap") == std::string::npos ||
      a.back().find("sys_exit_munmap") == std::string::npos ||
      a.back().find("args->ret >= 0") == std::string::npos)
    throw std::runtime_error(
        "mmap requires successful exit and matching munmap");
  c.target.pid_namespace = "pid:[4026533838]";
  c.target.namespace_pid = 17;
  a = build_runner_argv(c, tools);
  if (a.back().find("((struct pid_namespace*)") == std::string::npos ||
      a.back().find("->ns.inum == 4026533838") == std::string::npos ||
      a.back().find("->nr == 17") == std::string::npos ||
      a.back().find("nsecs,4321,") == std::string::npos ||
      a.back().find("DROP_PID") != std::string::npos ||
      a.back().find("offsetof(struct pid,numbers)") == std::string::npos ||
      a.back().find("sizeof(struct upid)") == std::string::npos ||
      a.back().find("count()") != std::string::npos) {
    throw std::runtime_error(
        "Trace predicate must pin namespace and local PID");
  }
  c.kind = "BOLT_PROFILE";
  tools["perf2bolt"] = "/usr/bin/perf2bolt-16";
  a = build_runner_argv(c, tools);
  if (a.front() != "/usr/bin/perf2bolt-16" ||
      std::find(a.begin(), a.end(), "-aggregate-only") == a.end() ||
      std::find(a.begin(), a.end(), "-nl") == a.end())
    throw std::runtime_error(
        "Version-suffixed LLVM aliases require explicit aggregation mode");
  c.kind = "MEMORY_TRACE";
  c.target.pid_namespace = "pid:[invalid]";
  rejects([&] { build_runner_argv(c, tools); });
  c.target.pid_namespace.clear();
  c.target.namespace_pid = 0;
  {
    std::string text;
    for (int i = 0; i < 500; ++i)
      text += std::string(1500, 'x') + '\n';
    const auto dmesg = bounded_kernel_snapshot(text);
    std::size_t bytes = 0;
    for (const auto &line : dmesg.at("lines")) {
      if (line.get<std::string>().size() > 1024)
        throw std::runtime_error("Kernel line budget exceeded");
      bytes += line.get<std::string>().size();
    }
    if (dmesg.at("lines").size() > 200 || bytes > 65536 ||
        !dmesg.at("truncated").get<bool>())
      throw std::runtime_error("Kernel snapshot not bounded");
  }
  std::cout << "runner argv: fixed tool paths, Java heap, Python rate and "
               "script boundaries passed\n";
}
