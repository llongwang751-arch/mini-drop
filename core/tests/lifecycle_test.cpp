#include "drop/process.hpp"
#include "drop/runner.hpp"
#include <atomic>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <unistd.h>

int main() {
  using namespace dropcore;
  using namespace std::chrono_literals;
  const auto directory = std::filesystem::temp_directory_path() /
                         ("drop-lifecycle-" + std::to_string(getpid()));
  std::filesystem::create_directory(directory);
  RunnerContext c;
  c.directory = directory;
  c.kind = "SCRIPT";
  c.target = process_identity(getpid());
  c.duration_seconds = 1;
  std::atomic<bool> canceled = false;
  c.canceled = [&] { return canceled.load(); };
  bool validated = false, prepared = false;
  auto runner = make_managed_runner(
      [&](const RunnerContext &) { validated = true; },
      [&](const RunnerContext &) { prepared = true; },
      [&](const RunnerContext &context) {
        auto result =
            execute({"/usr/bin/sleep", "30"},
                    (directory / "child.log").string(), 60s, context.canceled);
        if (!result.canceled)
          throw std::runtime_error("Cancellation did not reach child process");
        throw std::runtime_error("TASK_CANCELED");
        return RunnerOutput{};
      });
  bool premature = false;
  try {
    runner->Start(c);
  } catch (const std::logic_error &) {
    premature = true;
  }
  if (!premature)
    throw std::runtime_error("Start without Prepare accepted");
  runner->Validate(c);
  runner->Prepare(c);
  runner->Start(c);
  if (!validated || !prepared || runner->Poll())
    throw std::runtime_error("Lifecycle phases skipped");
  const auto begin = std::chrono::steady_clock::now();
  canceled = true;
  runner->Stop();
  runner->Cleanup();
  if (std::chrono::steady_clock::now() - begin > 3s)
    throw std::runtime_error(
        "Stop did not terminate the process group promptly");
  if (read_file((directory / "runner.state").string()) != "CLEANED\n")
    throw std::runtime_error("Cleanup did not persist state");
  auto successful = make_managed_runner(
      [](const RunnerContext &) {}, [](const RunnerContext &) {},
      [](const RunnerContext &) {
        return RunnerOutput{
            {}, "ok", "json", "application/json", nlohmann::json::object()};
      });
  canceled = false;
  successful->Validate(c);
  successful->Prepare(c);
  successful->Start(c);
  while (!successful->Poll())
    std::this_thread::sleep_for(1ms);
  if (successful->Collect().name != "ok")
    throw std::runtime_error("Successful Collect lost result");
  successful->Cleanup();
  std::filesystem::remove_all(directory);
  std::cout << "lifecycle: prepare/start/poll, process-group cancellation, "
               "collect and cleanup passed\n";
}
