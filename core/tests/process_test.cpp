#include "drop/process.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <sched.h>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>

void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}

std::vector<unsigned> allowed_cpus() {
  cpu_set_t allowed;
  CPU_ZERO(&allowed);
  require(sched_getaffinity(0, sizeof(allowed), &allowed) == 0,
          "Cannot inspect allowed collector CPU mask");
  std::vector<unsigned> cpus;
  for (unsigned cpu = 0; cpu < CPU_SETSIZE; ++cpu)
    if (CPU_ISSET(cpu, &allowed))
      cpus.push_back(cpu);
  require(!cpus.empty(), "Collector has no allowed CPUs");
  return cpus;
}
std::string cpu_list(const std::vector<unsigned> &cpus) {
  std::string result;
  for (const auto cpu : cpus) {
    if (!result.empty())
      result += ',';
    result += std::to_string(cpu);
  }
  return result;
}

int main(int argc, char **argv) {
  if (argc == 2 && std::string(argv[1]) == "--affinity-child") {
    std::cout << cpu_list(allowed_cpus()) << '\n';
    return 0;
  }
  if (argc == 2 && std::string(argv[1]) == "--fork-child") {
    const auto child = fork();
    if (child < 0)
      return 3;
    if (child == 0) {
      usleep(250000);
      _exit(0);
    }
    int status{};
    return waitpid(child, &status, 0) == child && WIFEXITED(status) &&
                   WEXITSTATUS(status) == 0
               ? 0
               : 4;
  }
  if (argc == 2 && std::string(argv[1]) == "--threads-child") {
    std::thread first([] { sleep(5); });
    std::thread second([] { sleep(5); });
    first.join();
    second.join();
    return 0;
  }
  const auto directory = std::filesystem::temp_directory_path() /
                         ("drop-process-test-" + std::to_string(getpid()));
  std::filesystem::create_directory(directory);
  const auto output = (directory / "output").string();
  auto result = dropcore::execute(
      {"/usr/bin/printf", "%s", "$(touch /tmp/should-never-exist); hi"}, output,
      std::chrono::seconds(3));
  require(result.exit_code == 0, "printf failed");
  require(result.output == "$(touch /tmp/should-never-exist); hi",
          "argv was interpreted by shell");
  auto timeout = dropcore::execute({"/usr/bin/sleep", "5"}, output,
                                   std::chrono::seconds(0));
  require(timeout.timed_out, "Deadline did not terminate process");
  auto canceled =
      dropcore::execute({"/usr/bin/sleep", "5"}, output,
                        std::chrono::seconds(3), [] { return true; });
  require(canceled.canceled, "Cancellation did not terminate process");
  auto failure =
      dropcore::execute({"/usr/bin/false"}, output, std::chrono::seconds(3));
  require(failure.exit_code == 1, "Nonzero exit was lost");
  // An unrelated same-UID process must neither consume a runner's budget nor
  // receive its termination signal. This also runs as UID10001 in validation.
  const auto unrelated = fork();
  require(unrelated >= 0, "Cannot start unrelated same-UID process");
  if (unrelated == 0) {
    setpgid(0, 0);
    sleep(20);
    _exit(0);
  }
  setpgid(unrelated, unrelated);
  dropcore::ProcessLimits process_budget;
  process_budget.processes = 2;
  const auto cooperative = dropcore::execute(
      {"/proc/self/exe", "--fork-child"}, output, std::chrono::seconds(3),
      [] { return false; }, process_budget);
  require(cooperative.exit_code == 0 && !cooperative.process_limit_exceeded,
          "Unrelated same-UID tasks prevented bounded tool cooperation");
  process_budget.processes = 1;
  const auto excessive_processes = dropcore::execute(
      {"/proc/self/exe", "--fork-child"}, output, std::chrono::seconds(3),
      [] { return false; }, process_budget);
  require(excessive_processes.process_limit_exceeded &&
              !excessive_processes.timed_out,
          "Owned child process did not count against the process budget");
  const auto excessive_threads = dropcore::execute(
      {"/proc/self/exe", "--threads-child"}, output, std::chrono::seconds(3),
      [] { return false; }, process_budget);
  require(excessive_threads.process_limit_exceeded &&
              !excessive_threads.timed_out,
          "Owned threads did not count against the process budget");
  require(kill(unrelated, 0) == 0,
          "Runner cleanup signaled an unrelated same-UID process");
  kill(unrelated, SIGKILL);
  waitpid(unrelated, nullptr, 0);
  for (const unsigned invalid : {0U, 65U}) {
    process_budget.processes = invalid;
    bool rejected = false;
    try {
      dropcore::execute(
          {"/usr/bin/true"}, output, std::chrono::seconds(3),
          [] { return false; }, process_budget);
    } catch (const std::invalid_argument &) {
      rejected = true;
    }
    require(rejected, "Invalid process budget was accepted");
  }
  const auto inherited = allowed_cpus();
  const auto first = std::to_string(inherited.front());
  require(dropcore::parse_cpu_affinity("").empty(),
          "Empty CPU affinity must preserve the inherited mask");
  auto inherited_child = dropcore::execute(
      {"/proc/self/exe", "--affinity-child"}, output, std::chrono::seconds(3));
  require(inherited_child.exit_code == 0 &&
              inherited_child.output == cpu_list(inherited) + "\n",
          "Default collector CPU affinity changed the inherited mask");
  dropcore::ProcessLimits selected;
  selected.cpu_affinity = dropcore::parse_cpu_affinity(first);
  auto bound = dropcore::execute(
      {"/proc/self/exe", "--affinity-child"}, output, std::chrono::seconds(3),
      [] { return false; }, selected);
  require(bound.exit_code == 0 && bound.output == first + "\n",
          "Collector subprocess did not bind to the selected allowed CPU");
  require(allowed_cpus() == inherited,
          "Binding a collector child changed its parent's CPU mask");
  if (inherited.size() > 1) {
    const std::vector<unsigned> pair = {inherited.front(), inherited.back()};
    require(dropcore::parse_cpu_affinity(cpu_list(pair)) == pair,
            "Valid comma-separated allowed CPU subset was rejected");
  }
  const std::vector<std::string> invalid = {
      "," + first,           first + ",",
      first + ",," + first,  first + "," + first,
      "+" + first,           "-1",
      first + " ",           first + "-" + first,
      "4294967296",          std::to_string(CPU_SETSIZE),
      std::string("0\0", 2), std::string(513, '0')};
  for (const auto &value : invalid) {
    bool rejected = false;
    try {
      dropcore::parse_cpu_affinity(value);
    } catch (const std::invalid_argument &) {
      rejected = true;
    }
    require(rejected,
            "Malformed, duplicate or out-of-range CPU affinity accepted");
  }
  std::string excessive = first;
  for (int index = 0; index < 64; ++index)
    excessive += "," + first;
  bool rejected_count = false;
  try {
    dropcore::parse_cpu_affinity(excessive);
  } catch (const std::invalid_argument &) {
    rejected_count = true;
  }
  require(rejected_count, "More than 64 collector CPUs accepted");
  std::vector<std::vector<unsigned>> invalid_masks = {
      {inherited.front(), inherited.front()},
      {CPU_SETSIZE},
      std::vector<unsigned>(65, inherited.front())};
  cpu_set_t inherited_mask;
  CPU_ZERO(&inherited_mask);
  for (const auto cpu : inherited)
    CPU_SET(cpu, &inherited_mask);
  for (unsigned cpu = 0; cpu < CPU_SETSIZE; ++cpu) {
    if (CPU_ISSET(cpu, &inherited_mask))
      continue;
    invalid_masks.push_back({cpu});
    bool rejected = false;
    try {
      dropcore::parse_cpu_affinity(std::to_string(cpu));
    } catch (const std::invalid_argument &) {
      rejected = true;
    }
    require(rejected, "CPU outside the current allowed mask accepted");
    break;
  }
  for (const auto &mask : invalid_masks) {
    bool rejected = false;
    selected.cpu_affinity = mask;
    try {
      dropcore::execute(
          {"/usr/bin/true"}, output, std::chrono::seconds(3),
          [] { return false; }, selected);
    } catch (const std::invalid_argument &) {
      rejected = true;
    }
    require(rejected, "Direct ProcessLimits bypassed CPU subset validation");
  }
  chmod(directory.c_str(), 0700);
  const auto nested = directory / "drop-nested-tool";
  std::ofstream(nested) << "#!/bin/sh\necho trusted-nested-tool\n";
  chmod(nested.c_str(), 0700);
  dropcore::ProcessLimits limits;
  limits.executable_path_prefix = directory.string();
  result = dropcore::execute(
      {"/bin/sh", "-c", "drop-nested-tool"}, output, std::chrono::seconds(3),
      [] { return false; }, limits);
  require(result.exit_code == 0 && result.output == "trusted-nested-tool\n",
          "Nested tool did not use the trusted directory");
  for (const bool writable : {false, true}) {
    limits.executable_path_prefix =
        directory.string() + (writable ? "" : ":/usr/bin");
    if (writable)
      chmod(directory.c_str(), 0777);
    bool rejected = false;
    try {
      dropcore::execute(
          {"/usr/bin/true"}, output, std::chrono::seconds(3),
          [] { return false; }, limits);
    } catch (const std::invalid_argument &) {
      rejected = true;
    }
    require(rejected, "Unsafe nested executable search path was accepted");
  }
  chmod(directory.c_str(), 0700);
  std::filesystem::remove_all(directory);
  std::cout
      << "process: argv/deadline/cancel/exit, owned process/thread budgets, "
         "same-UID isolation, inherited and child-only "
         "CPU masks, affinity bounds and trusted nested PATH passed\n";
}
