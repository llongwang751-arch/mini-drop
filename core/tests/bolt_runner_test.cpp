// 显式导入的诊断样本，只测试分类逻辑，不冒充现场采样结果。
#include "drop/runner.hpp"
#include "drop/bolt.hpp"
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <sys/sysmacros.h>
#include <unistd.h>

namespace {
void number(std::string &output, unsigned offset, std::uint64_t value,
            unsigned bytes) {
  for (unsigned index = 0; index < bytes; ++index)
    output.at(offset + index) = static_cast<char>(value >> (index * 8));
}
std::string imported_mapping(const dropcore::ProcessIdentity &target) {
  std::string body(72 + target.executable.size() + 1, 0);
  while (body.size() % 8)
    body.push_back(0);
  number(body, 0, 10, 4);
  number(body, 4, 2, 2);
  number(body, 6, body.size(), 2);
  number(body, 8, target.pid, 4);
  number(body, 12, target.pid, 4);
  number(body, 24, 4096, 8);
  number(body, 40, major(target.executable_device), 4);
  number(body, 44, minor(target.executable_device), 4);
  number(body, 48, target.executable_inode, 8);
  number(body, 64, 5, 4);
  body.replace(72, target.executable.size(), target.executable);
  std::string data(104, 0);
  data.replace(0, 8, "PERFILE2");
  number(data, 8, 104, 8);
  number(data, 40, 104, 8);
  number(data, 48, body.size(), 8);
  return data + body;
}
} // 结束匿名命名空间

int main() {
  using namespace dropcore;
  const std::string sensitive = "/private/fixture/target?token=IMPORTED_SECRET";
  const auto no_match = classify_bolt_failure(
      "PERF2BOLT-ERROR: failed to match build-id from perf output. " + sensitive);
  if (no_match != "BOLT_BINARY_SAMPLES_UNAVAILABLE")
    throw std::runtime_error("Build identity and no-sample failure lost");
  if (classify_bolt_failure("PERF-ERROR: return code 2 " + sensitive) !=
      "BOLT_PERF_CONVERSION_FAILED")
    throw std::runtime_error("Nested perf conversion failure lost");
  if (classify_bolt_failure("BOLT-ERROR: unsupported input " + sensitive) !=
          "BOLT_AGGREGATION_FAILED" ||
      classify_bolt_failure(std::string(16385, 'x')) !=
          "BOLT_AGGREGATION_FAILED")
    throw std::runtime_error("Unknown and bounded diagnostic fallback lost");
  for (const auto &code : {no_match, std::string("BOLT_PERF_CONVERSION_FAILED"),
                           std::string("BOLT_AGGREGATION_FAILED"),
                           std::string("BOLT_TARGET_MAPPING_UNVERIFIED")}) {
    const ProfilerExecutionFailure error(1, code);
    if (std::string(error.what()).find("IMPORTED_SECRET") != std::string::npos ||
        std::string(error.what()).find("/private/") != std::string::npos ||
        error.error_code != code || error.exit_code != 1)
      throw std::runtime_error("BOLT category exposed private diagnostics");
  }
  const std::string id(40, 'a');
  const std::string target_path = "/owned fixture/binary";
  if (parse_bolt_build_id(id + "\n") != id ||
      !parse_bolt_build_id(std::string(39, 'a')).empty() ||
      !parse_bolt_build_id(id + " IMPORTED_SECRET").empty() ||
      valid_bolt_target_path("/owned/binary,additional") ||
      valid_bolt_target_path("/owned/binary\ninjected") ||
      valid_bolt_target_path("/owned/binary (deleted)"))
    throw std::runtime_error("BOLT build identity input boundaries lost");
  if (match_bolt_build_ids("", target_path, id) != BoltBuildIdMatch::missing ||
      match_bolt_build_ids(id + " " + target_path + "\n", target_path, id) !=
          BoltBuildIdMatch::matched ||
      match_bolt_build_ids(std::string(40, 'b') + " " + target_path + "\n",
                           target_path, id) != BoltBuildIdMatch::conflict ||
      match_bolt_build_ids(id + " " + target_path + "\n" +
                               std::string(40, 'b') + " " + target_path + "\n",
                           target_path, id) != BoltBuildIdMatch::conflict ||
      match_bolt_build_ids(std::string(16385, 'x'), target_path, id) !=
          BoltBuildIdMatch::conflict)
    throw std::runtime_error("BOLT existing build-id conflict was overwritten");
  ProcessIdentity target;
  target.pid = 4321;
  target.executable = target_path;
  target.executable_device = makedev(8, 2);
  target.executable_inode = 12345;
  const auto path = std::filesystem::temp_directory_path() /
                    ("drop-bolt-imported-metadata-" + std::to_string(getpid()));
  const auto data = imported_mapping(target);
  const auto accepted = [&](const std::string &fixture,
                             const ProcessIdentity &identity,
                             std::uint64_t budget = 1024) {
    std::ofstream(path, std::ios::binary | std::ios::trunc) << fixture;
    return verify_bolt_perf_mapping(path, identity, budget);
  };
  if (!accepted(data, target))
    throw std::runtime_error("Imported complete native MMAP2 rejected");
  for (const auto offset : {112U, 144U, 148U, 152U}) {
    auto wrong = data;
    wrong[offset] ^= 1; // 覆盖PID过期、主次设备号不同或inode不同等身份不匹配情况。
    if (accepted(wrong, target))
      throw std::runtime_error("BOLT accepted another recorded target identity");
  }
  auto other = target;
  other.executable = "/another/file";
  auto no_inode = data;
  number(no_inode, 108, 1U << 14, 2);
  auto truncated_name = data;
  for (unsigned index = 176; index < truncated_name.size(); ++index)
    truncated_name[index] = 'x';
  auto oversized_data = data;
  number(oversized_data, 48, 0xffffffffffffffffULL, 8);
  auto short_record = data;
  number(short_record, 110, 7, 2);
  if (accepted(data, other) || accepted(no_inode, target) ||
      accepted(truncated_name, target) || accepted(oversized_data, target) ||
      accepted(short_record, target) || accepted(data.substr(0, 100), target) ||
      accepted(data, target, 100))
    throw std::runtime_error("BOLT malformed/unverifiable mapping accepted");
  std::filesystem::remove(path);
}
