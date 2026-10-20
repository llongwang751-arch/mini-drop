// 分配器uprobe的短路径别名接口，用设备号/inode验证真实库身份。
// 它只解决附加路径兼容问题，真实可用性仍需Runner的探测、授权与事件验证。

#pragma once
#include "drop/target.hpp"
#include <string>
#include <sys/types.h>

namespace dropcore {
// BCC旧版uprobe事件名包含目标路径。使用root持有的短路径别名，
// 避免容器/proc/<pid>/root长路径超出内核允许的事件名长度。
// 别名必须指向已选中库文件的同一inode，不能复制一份库来冒充采集目标。
class AllocatorProbeAlias {
public:
  AllocatorProbeAlias(const ProcessIdentity &, std::string library);
  ~AllocatorProbeAlias();
  AllocatorProbeAlias(const AllocatorProbeAlias &) = delete;
  AllocatorProbeAlias &operator=(const AllocatorProbeAlias &) = delete;
  const std::string &library() const { return library_; }
  const std::string &target_path() const { return target_path_; }
  void verify() const;

private:
  void cleanup() noexcept;
  ProcessIdentity target_;
  std::string library_, name_, target_path_;
  int parent_ = -1, directory_ = -1, library_fd_ = -1;
  dev_t directory_device_ = 0, link_device_ = 0;
  ino_t directory_inode_ = 0, link_inode_ = 0;
};
} // 结束 dropcore 命名空间
