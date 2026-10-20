// Bounded two-thread workload for actual PID/TID/time/DSO metadata evidence.
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <pthread.h>
#include <thread>

namespace {
std::atomic<bool> running{true};
void stop(int) { running = false; }
__attribute__((noinline)) double transform(double value) {
  for (int i = 0; i < 20000; ++i)
    value = std::sqrt(value * value + .321) + std::sin(value);
  return value;
}
void worker(const char *name) {
  pthread_setname_np(pthread_self(), name);
  volatile double observed = .1;
  while (running) {
    observed = transform(observed);
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
}
} // namespace
int main() {
  std::signal(SIGTERM, stop);
  std::signal(SIGINT, stop);
  std::thread first(worker, "fixture-alpha");
  std::thread second(worker, "fixture-beta");
  first.join();
  second.join();
}
