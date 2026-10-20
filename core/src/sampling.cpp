#include "drop/sampling.hpp"

#include <algorithm>
#include <array>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace dropcore {
PerfLossStats scan_perf_loss(const std::filesystem::path &path) {
  PerfLossStats stats;
  try {
    const auto bytes = std::filesystem::file_size(path);
    if (bytes < 56 || bytes > 512ULL * 1024 * 1024)
      return stats;
    std::ifstream file(path, std::ios::binary);
    std::array<unsigned char, 56> header{};
    file.read(reinterpret_cast<char *>(header.data()), header.size());
    if (!file)
      return stats;
    const std::string magic(reinterpret_cast<char *>(header.data()), 8);
    if (magic != "PERFILE2" && magic != "2ELIFREP")
      return stats;
    const bool little = magic == "PERFILE2";
    const auto number = [little](const unsigned char *source, int width) {
      std::uint64_t value = 0;
      for (int index = 0; index < width; ++index) {
        const auto shift = (little ? index : width - index - 1) * 8;
        value |= static_cast<std::uint64_t>(source[index]) << shift;
      }
      return value;
    };
    const auto header_size = number(header.data() + 8, 8);
    const auto offset = number(header.data() + 40, 8);
    const auto length = number(header.data() + 48, 8);
    if (header_size < 56 || offset < header_size || offset > bytes ||
        length > bytes - offset)
      return stats;
    const auto add = [](std::uint64_t &total, std::uint64_t amount) {
      if (amount > std::numeric_limits<std::uint64_t>::max() - total)
        throw std::runtime_error("Perf loss counter overflow");
      total += amount;
    };
    file.seekg(static_cast<std::streamoff>(offset));
    std::uint64_t consumed = 0, records = 0;
    while (consumed < length) {
      if (length - consumed < 8 || ++records > 2000000)
        return stats;
      std::array<unsigned char, 24> record{};
      file.read(reinterpret_cast<char *>(record.data()), 8);
      if (!file)
        return stats;
      const auto type = number(record.data(), 4);
      const auto size = number(record.data() + 6, 2);
      if (size < 8 || size > length - consumed)
        return stats;
      if (type == 81) // PERF_RECORD_COMPRESSED may contain nested loss records.
        return stats;
      if (type == 9) {
        ++stats.samples;
      } else if (type == 2 || type == 13) {
        const int required = type == 2 ? 24 : 16;
        if (size < static_cast<std::uint64_t>(required))
          return stats;
        file.read(reinterpret_cast<char *>(record.data() + 8), required - 8);
        if (!file)
          return stats;
        if (type == 2)
          add(stats.lost_records, number(record.data() + 16, 8));
        else
          add(stats.hardware_lost_samples, number(record.data() + 8, 8));
      }
      consumed += size;
      file.seekg(static_cast<std::streamoff>(offset + consumed));
      if (!file)
        return stats;
    }
    stats.complete = true;
  } catch (const std::exception &) {
    // Unknown/truncated data must never be reported as zero loss.
    stats.complete = false;
  }
  return stats;
}
void accumulate_thread_cpu(const ThreadCpuSnapshot &previous,
                           const ThreadCpuSnapshot &current,
                           ThreadCpuSnapshot &totals) {
  for (const auto &[tid, sample] : current) {
    const auto before = previous.find(tid);
    const auto old_ticks = before == previous.end() ? 0 : before->second.ticks;
    if (sample.ticks <= old_ticks)
      continue; // Exited/reused/reset thread, never subtract unsigned ticks.
    auto &accumulated = totals[tid];
    accumulated.name = sample.name;
    accumulated.ticks += sample.ticks - old_ticks;
  }
}
namespace {
std::string safe_frame(std::string frame) {
  for (auto &c : frame)
    if (c == ';' || c == '\n' || c == '\r' || c == '\0')
      c = '_';
  return frame.substr(0, 512);
}
} // namespace
std::map<std::string, std::uint64_t> parse_perf_script(std::istream &input) {
  std::map<std::string, std::uint64_t> stacks;
  std::vector<std::string> frames;
  std::string line, process = "process";
  auto flush = [&] {
    if (frames.empty())
      return;
    std::string key = safe_frame(process);
    std::reverse(frames.begin(), frames.end());
    for (const auto &frame : frames)
      key += ";" + frame;
    ++stacks[key];
    frames.clear();
    if (stacks.size() > 50000)
      throw std::runtime_error("Stack budget exceeded");
  };
  while (std::getline(input, line)) {
    if (line.size() > 4096)
      throw std::runtime_error("perf script line too long");
    if (line.empty()) {
      flush();
      continue;
    }
    if (line.front() == '#')
      continue;
    if (line.front() != ' ' && line.front() != '\t') {
      flush();
      std::istringstream header(line);
      header >> process;
      continue;
    }
    std::istringstream frame(line);
    std::string address;
    frame >> address;
    if (address.empty() || address.find_first_not_of(
                               "0123456789abcdefABCDEF") != std::string::npos) {
      // perf right-aligns command names by default, so event headers may also
      // have leading whitespace. They are distinct from hexadecimal frames.
      if (line.find(':') != std::string::npos && !address.empty()) {
        flush();
        process = address;
      }
      continue;
    }
    std::string symbol;
    std::getline(frame >> std::ws, symbol);
    auto dso = symbol.rfind(" (");
    if (dso != std::string::npos)
      symbol.resize(dso);
    auto offset = symbol.rfind("+0x");
    if (offset != std::string::npos)
      symbol.resize(offset);
    if (!symbol.empty())
      frames.push_back(safe_frame(symbol));
    if (frames.size() > 127)
      throw std::runtime_error("Call stack depth exceeds analysis budget");
  }
  flush();
  if (stacks.empty())
    throw std::runtime_error("No call stacks were produced by perf");
  return stacks;
}

void fold_perf_script(const std::filesystem::path &input,
                      const std::filesystem::path &output) {
  std::ifstream file(input);
  if (!file)
    throw std::runtime_error("Cannot read perf script output");
  auto stacks = parse_perf_script(file);
  std::ofstream folded(output);
  for (const auto &[stack, count] : stacks)
    folded << stack << ' ' << count << '\n';
  if (!folded)
    throw std::runtime_error("Cannot write folded artifact");
}
} // namespace dropcore
