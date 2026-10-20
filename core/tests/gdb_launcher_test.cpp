// 确定性的导入GDB替身，验证内核过滤及SHA策略，不表示真实采集。
#include "drop/artifact.hpp"
#include "drop/process.hpp"
#include "drop/target.hpp"
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
int main(int argc, char **argv) {
  if (argc != 3)
    return 2;
  namespace fs = std::filesystem;
  using namespace dropcore;
  const auto launcher = fs::canonical(argv[1]), child = fs::canonical(argv[2]);
  const auto root = fs::temp_directory_path() /
                    ("drop-gdb-launcher-" + std::to_string(getpid()));
  if (!fs::create_directory(root))
    throw std::runtime_error("Launcher fixture already exists");
  chmod(root.c_str(), 0700);
  struct Cleanup {
    fs::path path;
    ~Cleanup() { fs::remove_all(path); }
  } cleanup{root};
  const auto payload = root / "payload", manifest = root / "manifest.json";
  std::ofstream(payload) << "explicit imported policy fixture";
  auto value = nlohmann::json{
      {"tools",
       {{"gdb",
         {{"path", child.string()},
          {"sha256", sha256_file(child.string())},
          {"support_files",
           {{launcher.string(), sha256_file(launcher.string())}}}}}}}};
  const auto save = [&] {
    std::ofstream(manifest) << value.dump();
    chmod(manifest.c_str(), 0600);
  };
  const auto target = process_identity(getpid());
  const auto identity =
      nlohmann::json{{"pid", target.pid},
                     {"uid", target.uid},
                     {"start_time_ticks", target.start_time_ticks},
                     {"pid_namespace", target.pid_namespace},
                     {"namespace_pid", target.namespace_pid},
                     {"executable_device", target.executable_device},
                     {"executable_inode", target.executable_inode}};
  const auto execute_arguments =
      [&](const fs::path &metadata, const nlohmann::json &selected,
          const std::vector<std::string> &arguments) {
        ProcessLimits limits;
        limits.nested_tool_manifest_path = metadata.string();
        if (!selected.is_null())
          limits.nested_tool_target_identity = selected.dump();
        return execute(
            arguments, (root / "result").string(), std::chrono::seconds(3),
            [] { return false; }, limits);
      };
  const std::vector<std::string> arguments{launcher.string(),
                                           "-x=" + payload.string(), "-p",
                                           std::to_string(target.pid)};
  const auto execute_case = [&](const fs::path &metadata) {
    return execute_arguments(metadata, identity, arguments);
  };
  save();
  auto passed = execute_case(manifest);
  if (passed.exit_code || passed.output != "launcher filter passed\n")
    throw std::runtime_error("Actual launcher filter failed");
  auto long_pid = arguments;
  long_pid[2] = "--pid";
  if (execute_arguments(manifest, identity, long_pid).exit_code)
    throw std::runtime_error("Canonical long PID option rejected");
  for (const auto &key : {"pid", "uid", "start_time_ticks", "namespace_pid",
                          "executable_device", "executable_inode"}) {
    auto changed = identity;
    changed[key] = changed.at(key).get<std::uint64_t>() + 1;
    if (execute_arguments(manifest, changed, arguments).exit_code != 126)
      throw std::runtime_error("Changed pinned target accepted");
  }
  auto changed_namespace = identity;
  changed_namespace["pid_namespace"] = "pid:[1]";
  if (execute_arguments(manifest, changed_namespace, arguments).exit_code !=
          126 ||
      execute_arguments(manifest, nullptr, arguments).exit_code != 126)
    throw std::runtime_error("Missing or changed namespace metadata accepted");
  for (const auto &number : {"", "0", "1", "+2", " 2", "02", "2;id",
                             "2147483648", "999999999999999999999"}) {
    auto changed = arguments;
    changed.back() = number;
    if (execute_arguments(manifest, identity, changed).exit_code != 126)
      throw std::runtime_error("Invalid PID accepted");
  }
  for (const auto &alias : {"--pid=2", "-p2", "--pi=2", "--p=2"}) {
    auto changed = arguments;
    changed.push_back(alias);
    if (execute_arguments(manifest, identity, changed).exit_code != 126)
      throw std::runtime_error("Alternate duplicate PID spelling accepted");
  }
  auto duplicate = arguments;
  duplicate.insert(duplicate.end(), {"--pid", std::to_string(target.pid)});
  if (execute_arguments(manifest, identity, duplicate).exit_code != 126 ||
      execute_arguments(manifest, identity,
                        {launcher.string(), payload.string()})
              .exit_code != 126)
    throw std::runtime_error("Missing or duplicated PID accepted");
  auto override = arguments;
  override.push_back("--exec=/tmp/arbitrary");
  if (execute_arguments(manifest, identity, override).exit_code != 126)
    throw std::runtime_error("Target executable override accepted");
  const int caller = open(payload.c_str(), O_RDWR | O_CLOEXEC);
  if (caller < 0)
    throw std::runtime_error("Launcher filter escaped into its parent");
  close(caller);
  value["tools"]["gdb"]["sha256"] = std::string(64, '0');
  save();
  if (execute_case(manifest).exit_code != 126)
    throw std::runtime_error("Incorrect GDB bytes accepted");
  value["tools"]["gdb"]["sha256"] = sha256_file(child.string());
  value["tools"]["gdb"]["support_files"][launcher.string()] =
      std::string(64, '0');
  save();
  if (execute_case(manifest).exit_code != 126)
    throw std::runtime_error("Incorrect launcher bytes accepted");
  value["tools"]["gdb"]["support_files"][launcher.string()] =
      sha256_file(launcher.string());
  save();
  chmod(manifest.c_str(), 0622);
  if (execute_case(manifest).exit_code != 126)
    throw std::runtime_error("Writable manifest accepted");
  chmod(manifest.c_str(), 0600);
  const auto alias = root / "symlink-manifest";
  fs::create_symlink(manifest, alias);
  if (execute_case(alias).exit_code != 126)
    throw std::runtime_error("Symlink manifest accepted");
  const auto hardlink = root / "hardlink-manifest";
  fs::create_hard_link(manifest, hardlink);
  if (execute_case(manifest).exit_code != 126)
    throw std::runtime_error("Hardlink manifest accepted");
  fs::remove(hardlink);
  const auto fifo = root / "fifo-manifest";
  if (mkfifo(fifo.c_str(), 0600))
    throw std::runtime_error("Cannot create FIFO refusal fixture");
  if (execute_case(fifo).exit_code != 126)
    throw std::runtime_error("FIFO manifest accepted or blocked");
  if (execute_case(manifest).exit_code)
    throw std::runtime_error("Refusal contaminated next valid launch");
  std::cout << "GDB launcher: actual filter, fixed proc sysroot, pinned "
               "target, PID/SHA/file-policy refusals passed\n";
}
