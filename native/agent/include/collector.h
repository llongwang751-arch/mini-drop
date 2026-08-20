#pragma once

#include "config.h"
#include "task.h"

#include <atomic>
#include <memory>
#include <string>

namespace mini_drop_native {

class Collector {
 public:
  virtual ~Collector() = default;
  virtual std::string name() const = 0;
  virtual int profiler_type() const = 0;
  virtual bool available() const { return true; }
  virtual TaskResult collect(
      const Config& config,
      const Task& task,
      const std::atomic<bool>& stop_requested,
      std::atomic<bool>& cancel_requested) const = 0;
};

std::unique_ptr<Collector> make_perf_collector();
std::unique_ptr<Collector> make_ebpf_io_collector();
std::unique_ptr<Collector> make_async_profiler_collector();
std::unique_ptr<Collector> make_go_pprof_collector();
std::unique_ptr<Collector> make_pyspy_collector();
std::unique_ptr<Collector> make_memory_collector();
std::unique_ptr<Collector> make_sys_metrics_collector();
std::unique_ptr<Collector> make_continuous_perf_collector();

}  // namespace mini_drop_native
