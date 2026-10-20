#pragma once
#include "drop/catalog.hpp"
#include <filesystem>
#include <string>
namespace dropcore {
bool cooperative_agent_kind(const std::string &kind);
// RAW files stay on the bounded shared filesystem. No credential, upload
// grant, process command or arbitrary path is stored in its local marker.
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
} // namespace dropcore
