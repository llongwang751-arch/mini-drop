#include "process_runner.h"

#include <chrono>
#include <thread>

#include <fcntl.h>
#include <signal.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

namespace mini_drop_native {
namespace {
using namespace std::chrono_literals;
}

ProcessGroupRunner::ProcessGroupRunner(
    const Config& config, const std::atomic<bool>& stop_requested)
    : config_(config), stop_requested_(stop_requested) {}

CommandResult ProcessGroupRunner::run(
    const std::vector<std::string>& argv,
    int timeout_sec,
    const std::filesystem::path& stderr_path,
    const std::atomic<bool>& cancel_requested) const {
  CommandResult result;
  const pid_t child = ::fork();
  if (child < 0) {
    result.error = "fork failed";
    return result;
  }
  if (child == 0) {
    ::setpgid(0, 0);
    apply_limits(timeout_sec);
    const int stderr_fd = ::open(
        stderr_path.c_str(), O_CREAT | O_WRONLY | O_TRUNC, 0640);
    if (stderr_fd >= 0) {
      ::dup2(stderr_fd, STDERR_FILENO);
      ::close(stderr_fd);
    }
    std::vector<char*> args;
    args.reserve(argv.size() + 1);
    for (const auto& item : argv) {
      args.push_back(const_cast<char*>(item.c_str()));
    }
    args.push_back(nullptr);
    ::execvp(args[0], args.data());
    _exit(127);
  }

  ::setpgid(child, child);
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(timeout_sec);
  int status = 0;
  while (true) {
    const pid_t waited = ::waitpid(child, &status, WNOHANG);
    if (waited == child) {
      if (WIFEXITED(status)) {
        result.exit_code = WEXITSTATUS(status);
      } else if (WIFSIGNALED(status)) {
        result.exit_code = 128 + WTERMSIG(status);
      }
      return result;
    }
    if (waited < 0) {
      result.error = "waitpid failed";
      return result;
    }
    if (cancel_requested.load() || stop_requested_.load()) {
      result.cancelled = true;
      terminate_group(child);
      return result;
    }
    if (std::chrono::steady_clock::now() >= deadline) {
      result.timed_out = true;
      terminate_group(child);
      return result;
    }
    std::this_thread::sleep_for(100ms);
  }
}

void ProcessGroupRunner::apply_limits(int timeout_sec) const {
  struct rlimit cpu_limit {
    static_cast<rlim_t>(timeout_sec + 5),
    static_cast<rlim_t>(timeout_sec + 10)
  };
  ::setrlimit(RLIMIT_CPU, &cpu_limit);

  struct rlimit address_limit {
    static_cast<rlim_t>(config_.max_memory_mb) * 1024 * 1024,
    static_cast<rlim_t>(config_.max_memory_mb) * 1024 * 1024
  };
  ::setrlimit(RLIMIT_AS, &address_limit);

  struct rlimit file_limit {
    static_cast<rlim_t>(config_.max_output_mb) * 1024 * 1024,
    static_cast<rlim_t>(config_.max_output_mb) * 1024 * 1024
  };
  ::setrlimit(RLIMIT_FSIZE, &file_limit);
}

void ProcessGroupRunner::terminate_group(int child) {
  ::kill(-child, SIGTERM);
  for (int i = 0; i < 20; ++i) {
    int status = 0;
    if (::waitpid(child, &status, WNOHANG) == child) {
      return;
    }
    std::this_thread::sleep_for(100ms);
  }
  ::kill(-child, SIGKILL);
  int status = 0;
  ::waitpid(child, &status, 0);
}

}  // namespace mini_drop_native
