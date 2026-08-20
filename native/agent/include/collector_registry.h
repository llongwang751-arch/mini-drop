#pragma once

#include "collector.h"

#include <memory>
#include <unordered_map>
#include <vector>

namespace mini_drop_native {

class CollectorRegistry {
 public:
  void add(std::unique_ptr<Collector> collector);
  const Collector* find(int profiler_type) const;
  std::vector<std::string> capabilities() const;

 private:
  std::unordered_map<int, std::unique_ptr<Collector>> collectors_;
};

CollectorRegistry make_default_collector_registry();

}  // namespace mini_drop_native
