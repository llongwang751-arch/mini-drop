// 目标请求、节点策略与真实进程身份的类型定义。
// Target表示“想采谁”，TargetPolicy表示“节点允许采谁”，ProcessIdentity表示“实际解析到谁”。
// 将三者分开，避免把用户声明的UID/PID误当成已经通过系统校验的身份。

#pragma once
#include <cstdint>
#include <filesystem>
#include <functional>
#include <nlohmann/json.hpp>
#include <set>
#include <string>

namespace dropcore {
struct Target {
  std::string type = "process", runtime = "docker", container_id;
  int pid = 0, container_pid = 0;
  std::uint64_t start_time_ticks = 0;
  unsigned int uid = 0;
  bool check_uid = false;
};
struct TargetPolicy {
  bool allow_host_processes = false, allow_containers = false,
       allow_host_resources = false;
  std::set<unsigned int> allowed_uids;
  std::set<std::string> allowed_container_ids;
  int controlled_pid = 0;
};
struct ProcessIdentity {
  int pid = 0;
  unsigned int uid = 0;
  std::uint64_t start_time_ticks = 0;
  std::string executable, pid_namespace;
  std::uint64_t executable_device = 0, executable_inode = 0;
  int namespace_pid = 0;
};
Target target_from_json(const nlohmann::json &parameters);
void validate_target(const Target &target);
ProcessIdentity process_identity(int pid,
                                 const std::filesystem::path &proc = "/proc");
void validate_identity(const Target &requested, const ProcessIdentity &actual,
                       const TargetPolicy &policy);
ProcessIdentity resolve_target(const Target &target, const TargetPolicy &policy,
                               const std::string &scratch);
bool same_identity(const ProcessIdentity &a, const ProcessIdentity &b);
TargetPolicy target_policy(int controlled_pid);
nlohmann::json target_policy_json(const TargetPolicy &);
} // 结束 dropcore 命名空间
