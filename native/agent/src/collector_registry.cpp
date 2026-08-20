#include "collector_registry.h"

#include <stdexcept>

namespace mini_drop_native {

void CollectorRegistry::add(std::unique_ptr<Collector> collector) {
  if (!collector) throw std::invalid_argument("collector is null");
  const int type = collector->profiler_type();
  if (collectors_.count(type) != 0) {
    throw std::invalid_argument("duplicate collector profiler type");
  }
  collectors_.emplace(type, std::move(collector));
}

const Collector* CollectorRegistry::find(int profiler_type) const {
  const auto it = collectors_.find(profiler_type);
  return it == collectors_.end() || !it->second->available()
      ? nullptr : it->second.get();
}

std::vector<std::string> CollectorRegistry::capabilities() const {
  std::vector<std::string> result;
  result.reserve(collectors_.size());
  for (const auto& [_, collector] : collectors_) {
    if (collector->available()) result.push_back(collector->name());
  }
  return result;
}

CollectorRegistry make_default_collector_registry() {
  CollectorRegistry registry;
  registry.add(make_perf_collector());
  registry.add(make_ebpf_io_collector());
  registry.add(make_async_profiler_collector());
  registry.add(make_go_pprof_collector());
  registry.add(make_pyspy_collector());
  registry.add(make_memory_collector());
  registry.add(make_sys_metrics_collector());
  registry.add(make_continuous_perf_collector());
  return registry;
}

}  // namespace mini_drop_native
