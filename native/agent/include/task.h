#pragma once

#include <string>
#include <unordered_map>

namespace mini_drop_native {

struct UploadTarget {
  std::string put_url;
  long long expires_unix_ms = 0;
};

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
  std::string task_attempt_authority;
  std::string task_attempt_id;
  std::unordered_map<std::string, UploadTarget> upload_targets;
};

struct TaskResult {
  std::string task_id;
  bool ok = false;
  std::string error;
  std::string artifact_json;
  std::string task_attempt_authority;
  std::string task_attempt_id;
  std::string error_code;
};

}  // namespace mini_drop_native
