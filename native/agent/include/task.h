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
  int max_cpu_percent = 50;
  int max_memory_mb = 1024;
  int max_output_mb = 256;
  int max_duration = 15;
  int window_seconds = 60;
  int trigger_cpu_percent = 0;
  int trigger_consecutive_samples = 3;
  int trigger_wait_seconds = 0;
  std::string retention_tier = "standard";
  std::string callgraph = "fp";
  std::string event = "cpu-cycles:u";
  std::string container_name;
  std::string task_attempt_authority;
  std::string task_attempt_id;
  std::string traceparent;
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
