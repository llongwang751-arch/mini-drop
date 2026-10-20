// BOLT元数据处理接口，先确认目标ELF映射身份，再允许补齐优化所需信息。
// 解析器有输入边界且不把目标路径写进公开异常；没有证据时拒绝猜测。

#pragma once
#include "drop/target.hpp"
#include <cstdint>
#include <filesystem>
#include <string>

namespace dropcore {
enum class BoltBuildIdMatch { missing, matched, conflict };
// 这些函数只接收有大小限制的原生perf元数据；异常与日志只返回固定分类，
// 不带出原始profile内容或目标路径。
bool verify_bolt_perf_mapping(const std::filesystem::path &,
                              const ProcessIdentity &, std::uint64_t max_bytes);
std::string parse_bolt_build_id(const std::string &);
bool valid_bolt_target_path(const std::string &);
BoltBuildIdMatch match_bolt_build_ids(const std::string &listing,
                                     const std::string &target_path,
                                     const std::string &expected);
} // 结束 dropcore 命名空间
