#pragma once
#include <chrono>
#include <fstream>
#include <optional>
#include <sstream>
#include <string>
#include <unistd.h>

namespace mini_drop_native {
struct SelfCounters { double seconds, cpu_ticks, rss_mb, read_bytes, write_bytes; };
struct SelfRates { double cpu_percent, rss_mb, read_kb_s, write_kb_s; };

inline std::optional<SelfCounters> read_self_counters(const std::string& root = "/proc/self") {
  std::ifstream stat(root + "/stat"), io(root + "/io");
  std::string line;
  if (!std::getline(stat, line) || !io) return std::nullopt;
  const auto end = line.rfind(')');
  if (end == std::string::npos) return std::nullopt;
  std::istringstream fields(line.substr(end + 1));
  std::string field;
  double user = 0, system = 0, rss = 0;
  try {
    for (int index = 3; index <= 24; ++index) {
      if (!(fields >> field)) return std::nullopt;
      if (index == 14) user = std::stod(field);
      if (index == 15) system = std::stod(field);
      if (index == 24) rss = std::stod(field);
    }
  } catch (...) { return std::nullopt; }
  double read = -1, write = -1, value;
  while (io >> field >> value) {
    if (field == "read_bytes:") read = value;
    if (field == "write_bytes:") write = value;
  }
  const long page_size = sysconf(_SC_PAGESIZE);
  if (read < 0 || write < 0 || rss < 0 || user < 0 || system < 0 || page_size <= 0) return std::nullopt;
  return SelfCounters{std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(), user + system, rss * page_size / 1048576.0, read, write};
}

inline std::optional<SelfRates> self_rates(const SelfCounters& previous, const SelfCounters& current, long ticks_per_second) {
  const double dt = current.seconds - previous.seconds;
  if (dt <= 0 || ticks_per_second <= 0 || current.cpu_ticks < previous.cpu_ticks || current.read_bytes < previous.read_bytes || current.write_bytes < previous.write_bytes) return std::nullopt;
  return SelfRates{100.0 * (current.cpu_ticks - previous.cpu_ticks) / ticks_per_second / dt,
    current.rss_mb, (current.read_bytes - previous.read_bytes) / 1024.0 / dt,
    (current.write_bytes - previous.write_bytes) / 1024.0 / dt};
}
}  // namespace mini_drop_native
