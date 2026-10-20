#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>

namespace {
std::atomic<bool> running{true};
volatile double observed = 0;
void stop(int) { running = false; }
__attribute__((noinline)) double transform(std::uint64_t seed) {
  double value = static_cast<double>(seed % 1000) + 0.1;
  for (int i = 0; i < 4000; ++i)
    value = std::sqrt(value * value + 0.321) + std::sin(value);
  return value;
}
__attribute__((noinline)) double aggregate(std::uint64_t seed) {
  double result = 0;
  for (int i = 0; i < 20; ++i)
    result += transform(seed + i);
  return result;
}
__attribute__((noinline)) void benchmark() {
  std::uint64_t iteration = 0;
  while (running) {
    observed = aggregate(iteration++);
    // Keep the validation workload under roughly half of one CPU.
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
}
} // namespace

int main() {
  std::signal(SIGTERM, stop);
  std::signal(SIGINT, stop);
  std::cout << "Controlled Drop validation workload\n" << std::flush;
  benchmark();
}
