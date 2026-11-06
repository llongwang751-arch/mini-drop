// Teaching reference: CPU accounting for this program's OWN controlled work.
// No external PID, function stacks, shell, or storage credentials are accepted.
#include <charconv>
#include <cstdint>
#include <ctime>
#include <iomanip>
#include <iostream>
#include <string_view>
#include <system_error>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

bool own_cpu_ticks(std::uint64_t &ticks, std::uint64_t &rate) {
#if defined(_WIN32)
  // Windows CRT clock() measures elapsed time; use process CPU accounting.
  FILETIME created{}, exited{}, kernel{}, user{};
  if (!GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user))
    return false;
  const auto combine = [](const FILETIME &value) {
    return (static_cast<std::uint64_t>(value.dwHighDateTime) << 32) |
           value.dwLowDateTime;
  };
  const auto kernel_ticks = combine(kernel), user_ticks = combine(user);
  if (UINT64_MAX - kernel_ticks < user_ticks)
    return false;
  ticks = kernel_ticks + user_ticks;
  rate = UINT64_C(10000000); // FILETIME CPU accounting uses 100ns units.
  return true;
#else
  const auto value = std::clock();
  if (value == static_cast<std::clock_t>(-1) || CLOCKS_PER_SEC <= 0)
    return false;
  ticks = static_cast<std::uint64_t>(value);
  rate = static_cast<std::uint64_t>(CLOCKS_PER_SEC);
  return true;
#endif
}

int main(int argc, char **argv) {
  if (argc != 3 || std::string_view(argv[1]) != "--iterations") {
    std::cerr << "usage: collector --iterations N\n";
    return 2;
  }
  const std::string_view input(argv[2]);
  std::uint64_t iterations = 0;
  const auto parsed = std::from_chars(input.data(), input.data() + input.size(),
                                      iterations);
  if (parsed.ec != std::errc{} || parsed.ptr != input.data() + input.size() ||
      iterations < 1000 || iterations > 10000000) {
    std::cerr << "INVALID_ITERATIONS\n";
    return 2;
  }
  std::uint64_t start = 0, finish = 0, rate = 0, finish_rate = 0;
  if (!own_cpu_ticks(start, rate)) {
    std::cerr << "CPU_CLOCK_UNAVAILABLE\n";
    return 3;
  }
  std::uint64_t checksum = UINT64_C(1469598103934665603);
  for (std::uint64_t i = 0; i < iterations; ++i) {
    checksum ^= i + UINT64_C(0x9e3779b97f4a7c15);
    checksum *= UINT64_C(1099511628211); // Unsigned overflow is intentional.
    checksum ^= checksum >> 29;
  }
  if (!own_cpu_ticks(finish, finish_rate) || finish < start || rate != finish_rate) {
    std::cerr << "CPU_CLOCK_UNAVAILABLE\n";
    return 3;
  }
  std::cout << "{\"schema_version\":1,\"source\":\"controlled_self\","
               "\"unit\":\"cpu_ticks\",\"clock_ticks_per_second\":"
            << rate
            << ",\"total_ticks\":" << (finish - start)
            << ",\"iterations\":" << iterations << ",\"checksum\":\""
            << std::hex << std::setfill('0') << std::setw(16) << checksum
            << "\"}\n";
  return std::cout ? 0 : 3;
}
