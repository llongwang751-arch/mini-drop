#include "drop/sampling.hpp"

#include <algorithm>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <unistd.h>
#include <vector>

void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}
template <class F> void rejects(F &&action) {
  try {
    action();
  } catch (const std::runtime_error &) {
    return;
  }
  throw std::runtime_error("Invalid perf output accepted");
}
int main() {
  {
    // Explicit synthetic native perf headers; never presented as measured data.
    const auto fixture = std::filesystem::current_path() /
                         ("perf-loss-fixture-" + std::to_string(getpid()));
    const auto data = [](bool little, std::uint64_t ring_lost,
                         std::uint64_t hardware_lost) {
      std::vector<unsigned char> bytes(104);
      const std::string magic = little ? "PERFILE2" : "2ELIFREP";
      std::copy(magic.begin(), magic.end(), bytes.begin());
      const auto put = [&](std::size_t offset, std::uint64_t value, int size) {
        for (int index = 0; index < size; ++index) {
          const auto shift = (little ? index : size - 1 - index) * 8;
          bytes[offset + index] = static_cast<unsigned char>(value >> shift);
        }
      };
      put(8, 56, 8);
      put(40, 56, 8);
      put(48, 48, 8);
      put(56, 9, 4);
      put(62, 8, 2);
      put(64, 2, 4);
      put(70, 24, 2);
      put(80, ring_lost, 8);
      put(88, 13, 4);
      put(94, 16, 2);
      put(96, hardware_lost, 8);
      return bytes;
    };
    const auto write = [&](const std::vector<unsigned char> &bytes) {
      std::ofstream output(fixture, std::ios::binary);
      output.write(reinterpret_cast<const char *>(bytes.data()), bytes.size());
    };
    for (const bool little : {false, true}) {
      write(data(little, 3, 4));
      const auto stats = dropcore::scan_perf_loss(fixture);
      require(stats.complete && stats.samples == 1 && stats.lost_records == 3 &&
                  stats.hardware_lost_samples == 4,
              "Native perf loss record counts or endianness were lost");
    }
    write(data(true, 0, 0));
    const auto zero = dropcore::scan_perf_loss(fixture);
    require(zero.complete && zero.lost_records == 0 &&
                zero.hardware_lost_samples == 0,
            "Zero loss requires a complete native data scan");
    auto truncated = data(true, 3, 4);
    truncated.pop_back();
    write(truncated);
    require(!dropcore::scan_perf_loss(fixture).complete,
            "Truncated native data falsely reported known zero loss");
    auto malformed = data(true, 3, 4);
    malformed[70] = 1;
    write(malformed);
    require(!dropcore::scan_perf_loss(fixture).complete,
            "Malformed native record size accepted");
    auto compressed = data(true, 0, 0);
    compressed[56] = 81;
    write(compressed);
    require(!dropcore::scan_perf_loss(fixture).complete,
            "Compressed native records falsely proved zero loss");
    std::filesystem::remove(fixture);
  }
  std::istringstream script(
      "    drop_workload 42 100.00: cpu-clock:\n"
      "  000abc transform+0x10 (/usr/local/bin/drop_workload)\n"
      "  000def benchmark+0x20 (/usr/local/bin/drop_workload)\n\n"
      "drop_workload 42 100.01: cpu-clock:\n"
      "  000abc transform+0x10 (/usr/local/bin/drop_workload)\n"
      "  000def benchmark+0x20 (/usr/local/bin/drop_workload)\n\n"
      "drop_workload 42 100.02: cpu-clock:\n"
      "  000cde other;unsafe+0x3 (/usr/local/bin/drop_workload)\n");
  auto stacks = dropcore::parse_perf_script(script);
  require(stacks.size() == 2, "Identical stacks must aggregate");
  require(stacks.at("drop_workload;benchmark;transform") == 2,
          "Perf leaf-first frames must reverse");
  require(stacks.at("drop_workload;other_unsafe") == 1,
          "Frame delimiters must be escaped");
  rejects([] {
    std::istringstream empty("");
    dropcore::parse_perf_script(empty);
  });
  rejects([] {
    std::istringstream long_line(std::string(4097, 'a'));
    dropcore::parse_perf_script(long_line);
  });
  rejects([] {
    std::string data = "process 42 100.00: cpu-clock:\n";
    for (int i = 0; i < 128; ++i)
      data += "  abc deep (/bin/tool)\n";
    std::istringstream deep(data);
    dropcore::parse_perf_script(deep);
  });
  dropcore::ThreadCpuSnapshot before{{10, {"worker", 50}}},
      after{{10, {"worker", 70}}, {11, {"new-worker", 3}}}, totals;
  dropcore::accumulate_thread_cpu(before, after, totals);
  require(totals.at(10).ticks == 20,
          "Existing thread CPU delta was not measured");
  require(totals.at(11).ticks == 3, "New thread CPU time was lost");
  dropcore::accumulate_thread_cpu(after, {{10, {"reused", 1}}}, totals);
  require(totals.at(10).ticks == 20,
          "Reset thread produced negative/underflow CPU delta");
  std::cout << "sampling: 9 checks passed\n";
}
