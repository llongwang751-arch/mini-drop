// Explicitly instrumented toy fixture; never imported production data.
#include "drop/native_profiler.h"
#include <atomic>
#include <cmath>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <thread>
#include <unistd.h>
#include <vector>
std::atomic<bool> running = true;
void signal_stop(int) { running = false; }
__attribute__((noinline)) double native_fixture_hotspot(double seed) {
  for (int i = 0; i < 100000; ++i)
    seed = std::sin(seed) + std::sqrt(seed * seed + 1.01);
  return seed;
}
int main() {
  std::signal(SIGTERM, signal_stop);
  std::signal(SIGINT, signal_stop);
  const auto *directory = std::getenv("NATIVE_CONTROL_ROOT");
  if (!directory || drop_native_profiler_start(directory, 0) != 0)
    return 2;
  std::cout << getpid() << std::endl;
  double value = 1;
  std::vector<void *> retained;
  while (running) {
    value = native_fixture_hotspot(value);
    auto *allocation = std::malloc(4096 + (retained.size() % 128) * 1024);
    if (allocation) {
      static_cast<char *>(allocation)[0] = static_cast<char>(value);
      retained.push_back(allocation);
    }
    if (retained.size() > 1024) {
      for (std::size_t i = 0; i < 512; ++i)
        std::free(retained[i]);
      retained.erase(retained.begin(), retained.begin() + 512);
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  for (auto *memory : retained)
    std::free(memory);
  drop_native_profiler_stop();
  std::cout << value << '\n';
}
