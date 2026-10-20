// 原生分配器探针的路径适配：为BCC旧接口提供短而可核验的库文件别名。
// 必须核对原库与别名的设备号/inode一致，才能证明uprobe仍附加到正确的实际映射。
// 这里解决内核事件名长度与路径归属问题，不替代Runner的探针数量及目标权限校验。

#include "drop/allocator_probe.hpp"
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <stdexcept>
#include <sys/random.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>

namespace dropcore {
namespace {
bool same_file(const struct stat &left, const struct stat &right) {
  return left.st_dev == right.st_dev && left.st_ino == right.st_ino &&
         S_ISREG(left.st_mode) && S_ISREG(right.st_mode);
}
std::string alias_name() {
  std::array<unsigned char, 4> bytes{};
  std::size_t at = 0;
  while (at < bytes.size()) {
    const auto count = getrandom(bytes.data() + at, bytes.size() - at, 0);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0)
      throw std::runtime_error("Allocator alias entropy unavailable");
    at += static_cast<std::size_t>(count);
  }
  constexpr char hex[] = "0123456789abcdef";
  std::string result = "u.";
  for (const auto byte : bytes) {
    result += hex[byte >> 4];
    result += hex[byte & 15];
  }
  return result;
}
} // 结束匿名命名空间
AllocatorProbeAlias::AllocatorProbeAlias(const ProcessIdentity &target,
                                       std::string library)
    : target_(target), library_(std::move(library)) {
  try {
    const auto prefix = "/proc/" + std::to_string(target.pid) + "/root/";
    if (geteuid() != 0 || !library_.starts_with(prefix) ||
        library_.find_first_not_of(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/"
            "_-.+") != std::string::npos ||
        library_.find("/../") != std::string::npos)
      throw std::invalid_argument("Unsafe allocator alias source or owner");
    if (!same_identity(target_, process_identity(target_.pid)))
      throw std::runtime_error("TARGET_CHANGED");
    library_fd_ = open(library_.c_str(), O_PATH | O_NOFOLLOW | O_CLOEXEC);
    struct stat source {}, parent {};
    if (library_fd_ < 0 || fstat(library_fd_, &source) ||
        !S_ISREG(source.st_mode))
      throw std::runtime_error("Allocator alias requires a regular library");
    const auto temporary = prefix + "tmp";
    parent_ = open(temporary.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW |
                                        O_CLOEXEC);
    if (parent_ < 0 || fstat(parent_, &parent) || parent.st_uid != 0 ||
        ((parent.st_mode & 0022) && !(parent.st_mode & S_ISVTX)))
      throw std::runtime_error("Allocator alias needs a root-owned safe tmp");
    for (unsigned attempt = 0; attempt < 16; ++attempt) {
      name_ = alias_name();
      if (mkdirat(parent_, name_.c_str(), 0700) == 0)
        break;
      if (errno != EEXIST || attempt == 15) {
        name_.clear();
        throw std::runtime_error("Allocator alias directory unavailable");
      }
    }
    directory_ = openat(parent_, name_.c_str(),
                        O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    struct stat directory {}, link {};
    if (directory_ < 0 || fstat(directory_, &directory) ||
        directory.st_uid != 0 || (directory.st_mode & 0777) != 0700)
      throw std::runtime_error("Allocator alias directory identity changed");
    directory_device_ = directory.st_dev;
    directory_inode_ = directory.st_ino;
    if (symlinkat(library_.c_str(), directory_, "a") ||
        fstatat(directory_, "a", &link, AT_SYMLINK_NOFOLLOW) ||
        !S_ISLNK(link.st_mode) || link.st_uid != 0)
      throw std::runtime_error("Allocator alias link unavailable");
    link_device_ = link.st_dev;
    link_inode_ = link.st_ino;
    target_path_ = "/tmp/" + name_ + "/a";
    verify();
  } catch (...) {
    cleanup();
    throw;
  }
}
void AllocatorProbeAlias::verify() const {
  if (!same_identity(target_, process_identity(target_.pid)))
    throw std::runtime_error("TARGET_CHANGED");
  struct stat held {}, current {}, alias {}, directory {}, link {};
  if (library_fd_ < 0 || parent_ < 0 || directory_ < 0 ||
      fstat(library_fd_, &held) || stat(library_.c_str(), &current) ||
      fstatat(directory_, "a", &alias, 0) ||
      fstatat(directory_, "a", &link, AT_SYMLINK_NOFOLLOW) ||
      fstatat(parent_, name_.c_str(), &directory, AT_SYMLINK_NOFOLLOW) ||
      !same_file(held, current) || !same_file(held, alias) ||
      !S_ISDIR(directory.st_mode) || directory.st_uid != 0 ||
      (directory.st_mode & 0777) != 0700 ||
      directory.st_dev != directory_device_ ||
      directory.st_ino != directory_inode_ || !S_ISLNK(link.st_mode) ||
      link.st_uid != 0 || link.st_dev != link_device_ ||
      link.st_ino != link_inode_)
    throw std::runtime_error("Allocator alias identity changed");
}
void AllocatorProbeAlias::cleanup() noexcept {
  struct stat link {}, directory {};
  if (directory_ >= 0) {
    if (fstatat(directory_, "a", &link, AT_SYMLINK_NOFOLLOW) == 0 &&
        S_ISLNK(link.st_mode) && link.st_dev == link_device_ &&
        link.st_ino == link_inode_)
      (void)unlinkat(directory_, "a", 0);
    close(directory_);
    directory_ = -1;
  }
  if (parent_ >= 0) {
    if (!name_.empty() &&
        fstatat(parent_, name_.c_str(), &directory, AT_SYMLINK_NOFOLLOW) == 0 &&
        S_ISDIR(directory.st_mode) && directory.st_uid == 0 &&
        directory.st_dev == directory_device_ &&
        directory.st_ino == directory_inode_)
      (void)unlinkat(parent_, name_.c_str(), AT_REMOVEDIR);
    close(parent_);
    parent_ = -1;
  }
  if (library_fd_ >= 0) {
    close(library_fd_);
    library_fd_ = -1;
  }
}
AllocatorProbeAlias::~AllocatorProbeAlias() { cleanup(); }
} // 结束 dropcore 命名空间
