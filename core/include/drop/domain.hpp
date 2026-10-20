// 采集领域的轻量约束：基础参数、状态迁移、优先级与当前attempt匹配。
// 这些函数刻意不访问网络或数据库，便于把状态边界作为独立单元测试验证。
// 重试产生新attempt；旧attempt即使晚到成功结果，也不能回写当前任务。

#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace dropcore {
inline void validate_priority(int priority) {
  if (priority < -10 || priority > 10)
    throw std::invalid_argument("Priority must be between -10 and 10");
}

struct SamplingParameters {
  int pid = 0;
  int duration_seconds = 10;
  int frequency_hz = 99;
  std::string scope = "process";
};

inline void validate(const SamplingParameters &p) {
  if (p.pid < 0 || p.pid == 1)
    throw std::invalid_argument(
        "PID must be zero (controlled workload) or greater than one");
  if (p.duration_seconds < 1 || p.duration_seconds > 60)
    throw std::invalid_argument("Duration must be within 1..60 seconds");
  if (p.frequency_hz < 1 || p.frequency_hz > 199)
    throw std::invalid_argument("Frequency must be within 1..199 Hz");
  if (p.scope != "process")
    throw std::invalid_argument("Only process scope is supported");
}

inline std::vector<std::string> perf_record_argv(const SamplingParameters &p,
                                                 const std::string &output,
                                                 int actual_pid) {
  validate(p);
  if (actual_pid <= 1)
    throw std::invalid_argument("Resolved PID is unsafe");
  if (output.empty() || output.front() != '/' ||
      output.find("..") != std::string::npos)
    throw std::invalid_argument("Output path must be an absolute task path");
  return {"/usr/bin/perf",
          "record",
          "-e",
          "cpu-clock",
          "-F",
          std::to_string(p.frequency_hz),
          "-g",
          "-p",
          std::to_string(actual_pid),
          "-o",
          output,
          "--",
          "/usr/bin/sleep",
          std::to_string(p.duration_seconds)};
}

// 采集状态只按合法方向迁移；重复同态回报允许幂等，但终态不可复活。
// 分析状态由分析链路独立维护，不能用采集COLLECTED表示分析也成功。
inline bool collection_transition_allowed(const std::string &from,
                                          const std::string &to) {
  if (from == to)
    return true;
  if (from == "COLLECTED" || from == "FAILED" || from == "CANCELED")
    return false;
  if (to == "FAILED" || to == "CANCELED")
    return true;
  return (from == "CREATED" && to == "QUEUED") ||
         (from == "QUEUED" && to == "DELIVERED") ||
         (from == "DELIVERED" && to == "RUNNING") ||
         (from == "RUNNING" && to == "UPLOADING") ||
         (from == "UPLOADING" && to == "COLLECTED");
}

inline bool safe_identifier(const std::string &text) {
  if (text.empty() || text.size() > 128)
    return false;
  for (unsigned char c : text)
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
          (c >= '0' && c <= '9') || c == '-' || c == '_'))
      return false;
  return true;
}

// 迟到请求必须同时匹配当前执行身份；只匹配task_id不足以区分重试前后。
inline bool current_attempt_matches(std::int64_t requested,
                                    std::int64_t current) {
  return requested > 0 && requested == current;
}

} // 结束 dropcore 命名空间
