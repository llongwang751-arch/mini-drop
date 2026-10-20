// 采样质量与数据转换的类型声明，明确线程计数、原生perf事件和folded栈的区别。
// 未知或截断状态要随结果保留，不能用一个默认0掩盖采样质量不足。

#pragma once
#include <cstdint>
#include <filesystem>
#include <istream>
#include <map>
#include <string>

namespace dropcore {
struct ThreadCpuSample {
  std::string name;
  std::uint64_t ticks = 0;
};
using ThreadCpuSnapshot = std::map<int, ThreadCpuSample>;
struct PerfLossStats {
  bool complete = false;
  std::uint64_t samples = 0, lost_records = 0, hardware_lost_samples = 0;
};
PerfLossStats scan_perf_loss(const std::filesystem::path &input);
void accumulate_thread_cpu(const ThreadCpuSnapshot &previous,
                           const ThreadCpuSnapshot &current,
                           ThreadCpuSnapshot &totals);
std::map<std::string, std::uint64_t> parse_perf_script(std::istream &input);
void fold_perf_script(const std::filesystem::path &input,
                      const std::filesystem::path &output);
} // 结束 dropcore 命名空间
