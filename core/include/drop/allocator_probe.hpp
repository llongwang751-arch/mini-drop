#pragma once
#include "drop/target.hpp"
#include <string>
#include <sys/types.h>

namespace dropcore {
// BCC's legacy uprobe event names contain the target path. A root-owned short
// alias prevents a long container /proc/<pid>/root path overflowing that name.
// It refers to the exact selected library inode; it is never a copied library.
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
} // namespace dropcore
