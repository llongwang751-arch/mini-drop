// Synthetic, deterministic integer CPU workload for real perf/BOLT sampling.
// --once is a frozen equivalence input, never user-provided executable code.
#include <atomic>
#include <cstdint>
#include <csignal>
#include <iostream>
#include <string_view>
#include <unistd.h>

namespace {
std::atomic<bool> running = true;
void stop(int) { running = false; }
__attribute__((noinline)) std::uint64_t mix(std::uint64_t value,
                                           std::uint64_t salt) {
  value ^= value << 13;
  value ^= value >> 7;
  value ^= value << 17;
  return (value * 0x9e3779b97f4a7c15ULL) ^ salt;
}
__attribute__((noinline)) std::uint64_t hotspot(std::uint64_t seed) {
  for (std::uint64_t i = 0; i < 8000000; ++i) {
    seed = mix(seed, i);
    if ((seed & 63) == 0)
      seed = mix(seed, 0x846ca68b2309ULL);
    else
      seed += 0x517cc1b727220a95ULL;
  }
  return seed;
}
} // namespace

int main(int argc, char **argv) {
  if (argc == 2 && std::string_view(argv[1]) == "--once") {
    std::cout << std::hex << hotspot(0x123456789abcdefULL) << '\n';
    return 0;
  }
  if (argc != 1)
    return 2;
  std::signal(SIGTERM, stop);
  std::signal(SIGINT, stop);
  std::cout << getpid() << std::endl;
  std::uint64_t value = 0x123456789abcdefULL;
  while (running)
    value = hotspot(value);
  // Keeping the checksum observable prevents the sampling loop being erased.
  std::cout << std::hex << value << '\n';
}
