#pragma once

#include <filesystem>
#include <fstream>
#include <array>
#include <cstdint>
#include <string>

namespace mini_drop_native {

enum class PerfSamples { present, absent, invalid };

// Check PERF_RECORD_SAMPLE records in the uncompressed perf record file this
// collector produces. File size and mmap/comm metadata are not sample counts.
// Layout: Linux tools/perf/util/header.h and uapi/linux/perf_event.h.
inline PerfSamples inspect_perf_samples(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary | std::ios::ate);
  if (!input) return PerfSamples::invalid;
  const auto end = input.tellg();
  if (end < 72) return PerfSamples::invalid;
  const auto bytes = static_cast<std::uint64_t>(end);
  input.seekg(0);
  std::array<unsigned char, 72> header{};
  if (!input.read(reinterpret_cast<char*>(header.data()), header.size()))
    return PerfSamples::invalid;
  const std::string magic(reinterpret_cast<char*>(header.data()), 8);
  if (magic != "PERFILE2" && magic != "2ELIFREP") return PerfSamples::invalid;
  const bool little = magic == "PERFILE2";
  const auto number = [little](const unsigned char* data, unsigned size) {
    std::uint64_t value = 0;
    for (unsigned i = 0; i < size; ++i)
      value = (value << 8) | data[little ? size - i - 1 : i];
    return value;
  };
  const auto header_size = number(header.data() + 8, 8);
  const auto offset = number(header.data() + 40, 8);
  const auto size = number(header.data() + 48, 8);
  if (header_size < 72 || header_size > bytes || offset < header_size ||
      offset > bytes || size > bytes - offset) return PerfSamples::invalid;
  auto remaining = size;
  input.seekg(static_cast<std::streamoff>(offset));
  std::array<unsigned char, 8> record{};
  // Keep malformed/hostile data from monopolizing the Agent thread.
  for (unsigned records = 0; remaining && records < 5'000'000; ++records) {
    if (remaining < record.size() ||
        !input.read(reinterpret_cast<char*>(record.data()), record.size()))
      return PerfSamples::invalid;
    const auto record_size = number(record.data() + 6, 2);
    if (record_size < record.size() || record_size > remaining)
      return PerfSamples::invalid;
    if (number(record.data(), 4) == 9) // PERF_RECORD_SAMPLE
      return record_size > record.size() ? PerfSamples::present : PerfSamples::invalid;
    input.seekg(static_cast<std::streamoff>(record_size - record.size()), std::ios::cur);
    remaining -= record_size;
  }
  return remaining ? PerfSamples::invalid : PerfSamples::absent;
}

} // namespace mini_drop_native
