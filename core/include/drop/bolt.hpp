#pragma once
#include "drop/target.hpp"
#include <cstdint>
#include <filesystem>
#include <string>

namespace dropcore {
enum class BoltBuildIdMatch { missing, matched, conflict };
// These helpers accept only bounded, native perf metadata. No profile contents
// or target paths appear in exceptions or logs.
bool verify_bolt_perf_mapping(const std::filesystem::path &,
                              const ProcessIdentity &, std::uint64_t max_bytes);
std::string parse_bolt_build_id(const std::string &);
bool valid_bolt_target_path(const std::string &);
BoltBuildIdMatch match_bolt_build_ids(const std::string &listing,
                                     const std::string &target_path,
                                     const std::string &expected);
} // namespace dropcore
