// 仅在显式启用受控演示时启动的CPU负载，为现场采样提供可预测目标。
// 它不是线上业务，也不能用本负载的结果推导真实应用的请求延迟或收益。

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
    // 将验证负载控制在约半个CPU以内，避免测试程序持续挤占整核。
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
}
} // 结束匿名命名空间

int main() {
  std::signal(SIGTERM, stop);
  std::signal(SIGINT, stop);
  std::cout << "Controlled Drop validation workload\n" << std::flush;
  benchmark();
}
