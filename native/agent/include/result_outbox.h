#pragma once

#include "task.h"

#include <cstddef>
#include <filesystem>
#include <functional>
#include <vector>

namespace mini_drop_native {

struct OutboxEntry {
  std::filesystem::path path;
  TaskResult result;
};

class ResultOutbox {
 public:
  explicit ResultOutbox(std::filesystem::path directory,
                        std::size_t max_entries = 256);

  OutboxEntry enqueue(const TaskResult& result);
  std::vector<OutboxEntry> pending();
  void acknowledge(const OutboxEntry& entry) const;
  std::size_t replay(const std::function<bool(const TaskResult&)>& deliver);

 private:
  std::filesystem::path path_for(const std::string& task_id) const;
  void trim();

  std::filesystem::path directory_;
  std::size_t max_entries_;
};

}
