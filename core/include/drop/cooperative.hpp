// 显式协作采集的本地文件约定；用于应用主动接入的采集控制与安全清理。
// 目录归属、容量限制和恢复标记共同约束会话，不能把共享目录当作任意路径通道。

#pragma once
#include "drop/catalog.hpp"
#include <filesystem>
#include <string>
namespace dropcore {
bool cooperative_agent_kind(const std::string &kind);
// RAW保留在有容量边界的共享文件系统；本地标记只记录恢复所需的固定信息，
// 不存储凭据、上传授权、进程命令或任意路径，防止恢复文件扩大权限。
class CooperativeFiles {
public:
  CooperativeFiles(const Catalog &, std::filesystem::path root,
                   std::string agent, std::uint64_t maximum = 2147483648ULL,
                   bool bounded_filesystem = true);
  std::filesystem::path prepare(const std::filesystem::path &journal,
                                const std::string &kind,
                                std::int64_t attempt) const;
  std::filesystem::path directory(const std::filesystem::path &journal) const;
  std::filesystem::path raw(const std::filesystem::path &journal,
                            const std::string &name) const;
  void sync(const std::filesystem::path &journal, const std::string &name,
            std::uint64_t maximum) const;
  void remove(const std::filesystem::path &journal) const;
  std::uint64_t bytes(std::uint64_t maximum) const;
  unsigned sweep(const std::filesystem::path &journals,
                 std::uint64_t retention_seconds) const;
  bool bounded_filesystem() const { return bounded_; }
  void prepare_native_control(const std::filesystem::path &directory) const;

private:
  std::filesystem::path locate(const std::filesystem::path &journal,
                               bool required) const;
  const Catalog &catalog_;
  std::filesystem::path root_;
  std::string agent_, prefix_;
  bool bounded_ = false;
  std::uint64_t maximum_;
};
} // 结束 dropcore 命名空间
