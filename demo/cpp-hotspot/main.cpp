#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <iostream>
#include <thread>
#include <unistd.h>

namespace {
std::atomic<bool> running{true};
void stop(int) { running.store(false); }
}

std::uint64_t hot_loop(std::uint64_t seed) {
  std::uint64_t value = seed;
  for (std::uint64_t index = 0; index < 2'000'000; ++index) {
    value ^= value << 13;
    value ^= value >> 7;
    value ^= value << 17;
  }
  return value;
}

int main() {
  std::signal(SIGINT, stop);
  std::signal(SIGTERM, stop);
  std::uint64_t value = 0x5f3759df;
  std::cout << "C++ hotspot started, pid=" << ::getpid() << std::endl;
  while (running.load()) {
    value = hot_loop(value);
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  std::cout << value << std::endl;
  return 0;
}
