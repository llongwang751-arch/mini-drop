#include "perf_samples.h"
#include <cstdlib>
#include <iostream>
#include <vector>
#include <unistd.h>

using mini_drop_native::PerfSamples;
using mini_drop_native::inspect_perf_samples;

int main() {
  char filename[] = "/tmp/mini-drop-perf-test-XXXXXX";
  const auto fd = mkstemp(filename);
  if (fd < 0) return 1;
  close(fd);
  std::vector<unsigned char> data;
  auto put = [&](std::size_t at, std::uint64_t value, unsigned size = 8) {
    for (unsigned i = 0; i < size; ++i) data.at(at + i) = (value >> (8 * i)) & 255;
  };
  auto base = [&](std::size_t size) {
    data.assign(size, 0);
    const std::string magic = "PERFILE2";
    std::copy(magic.begin(), magic.end(), data.begin());
    put(8, 104); put(40, 104); put(48, size - 104);
  };
  auto check = [&](PerfSamples expected, const char* label) {
    { std::ofstream out(filename, std::ios::binary); out.write(reinterpret_cast<char*>(data.data()), data.size()); }
    if (inspect_perf_samples(filename) != expected) {
      std::cerr << label << '\n'; std::filesystem::remove(filename); std::exit(1);
    }
  };
  base(120); put(104, 9, 4); put(110, 16, 2);
  check(PerfSamples::present, "valid small sample must survive size below 16KiB");
  base(104); check(PerfSamples::absent, "header without records");
  base(20104); put(104, 1, 4); put(110, 20000, 2);
  check(PerfSamples::absent, "large mmap metadata is not a sample");
  put(110, 0, 2); check(PerfSamples::invalid, "zero-sized record cannot loop forever");
  base(120); put(104, 9, 4); put(110, 1000, 2);
  check(PerfSamples::invalid, "truncated sample rejected");
  put(48, UINT64_MAX); check(PerfSamples::invalid, "overflowing section rejected");
  base(104); data[0] = 'X'; check(PerfSamples::invalid, "wrong magic rejected");
  std::filesystem::remove(filename);
  std::cout << "perf sample validation: 7 cases passed\n";
}
