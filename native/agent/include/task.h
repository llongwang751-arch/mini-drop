#pragma once

#include <string>

namespace mini_drop_native {

struct Task {
  std::string id;
  int profiler_type = 0;
  int pid = 0;
  int hz = 99;
  int duration = 15;
  int timeout = 45;
  std::string callgraph = "fp";
  std::string event = "cpu-cycles:u";
  std::string container_name;
};

struct TaskResult {
  std::string task_id;
  bool ok = false;
  std::string error;
  std::string artifact_json;
};

}  // namespace mini_drop_native
