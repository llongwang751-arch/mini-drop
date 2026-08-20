#pragma once

#include "config.h"

#include <atomic>
#include <filesystem>
#include <string>
#include <vector>

namespace mini_drop_native {

struct CommandResult {
  int exit_code = -1;
  bool timed_out = false;
  bool cancelled = false;
  std::string error;
};

class ProcessGroupRunner {
 public:
  ProcessGroupRunner(const Config& config, const std::atomic<bool>& stop_requested);

  CommandResult run(
      const std::vector<std::string>& argv,
      int timeout_sec,
      const std::filesystem::path& stderr_path,
      const std::atomic<bool>& cancel_requested) const;

 private:
  void apply_limits(int timeout_sec) const;
  static void terminate_group(int child);

  const Config& config_;
  const std::atomic<bool>& stop_requested_;
};

}  // namespace mini_drop_native
