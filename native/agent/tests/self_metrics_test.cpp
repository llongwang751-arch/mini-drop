#include "self_metrics.h"
#include <cmath>
#include <iostream>
#include <stdexcept>
using namespace mini_drop_native;
void check(bool value) { if (!value) throw std::runtime_error("self metrics check failed"); }
int main() {
  SelfCounters a{10, 100, 12, 1024, 2048}, b{12, 150, 15, 3072, 6144};
  auto result = self_rates(a, b, 100);
  check(result && result->cpu_percent == 25 && result->rss_mb == 15 && result->read_kb_s == 1 && result->write_kb_s == 2);
  check(!self_rates(a, a, 100));
  check(!self_rates(a, b, 0));
  b.read_bytes = 0; check(!self_rates(a, b, 100));
  check(!read_self_counters("/this/path/does/not/exist"));
  auto live = read_self_counters(); check(live && live->rss_mb > 0 && live->cpu_ticks >= 0);
  std::cout << "self metrics cases passed\n";
}
