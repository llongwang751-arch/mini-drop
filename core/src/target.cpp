#include "drop/target.hpp"
#include "drop/artifact.hpp"
#include "drop/json.hpp"
#include "drop/process.hpp"
#include <algorithm>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

namespace fs = std::filesystem;
using json = nlohmann::json;
namespace dropcore {
Target target_from_json(const json &parameters) {
  Target t;
  t.pid = parameters.value("pid", 0);
  if (parameters.contains("target")) {
    const auto &v = parameters.at("target");
    if (!v.is_object())
      throw std::invalid_argument("Target must be an object");
    t.type = v.value("type", "process");
    t.pid = v.value("pid", t.pid);
    if (parameters.value("pid", 0) > 1 && v.value("pid", 0) > 1 &&
        parameters.at("pid") != v.at("pid"))
      throw std::invalid_argument("Top-level PID and target PID disagree");
    t.container_id = v.value("container_id", "");
    t.runtime = v.value("runtime", "docker");
    t.container_pid = v.value("container_pid", 0);
    t.start_time_ticks = v.value("start_time_ticks", std::uint64_t{0});
    t.uid = v.value("expected_uid", 0U);
    t.check_uid = v.value("check_uid", false);
  }
  return t;
}
void validate_target(const Target &t) {
  if (t.type != "process" && t.type != "host" && t.type != "container")
    throw std::invalid_argument("Unsupported target type");
  if (t.type == "host")
    return;
  if (t.type == "process" && t.pid <= 1)
    throw std::invalid_argument("PID must exceed one");
  if (t.type == "container") {
    if (t.runtime != "docker" && t.runtime != "containerd")
      throw std::invalid_argument("Unsupported container runtime");
    if (t.container_id.size() < 12 || t.container_id.size() > 64 ||
        t.container_id.find_first_not_of("0123456789abcdefABCDEF") !=
            std::string::npos)
      throw std::invalid_argument("Invalid container ID");
    if (t.container_pid < 0)
      throw std::invalid_argument("Invalid container PID");
  }
}
ProcessIdentity process_identity(int pid, const fs::path &proc) {
  if (pid <= 1)
    throw std::invalid_argument("Unsafe process ID");
  const auto base = proc / std::to_string(pid);
  const auto text = read_file((base / "stat").string(), 8192);
  const auto close = text.rfind(')');
  if (close == std::string::npos)
    throw std::runtime_error("Invalid process stat");
  std::istringstream fields(text.substr(close + 2));
  std::string value;
  for (int i = 0; i <= 19; ++i)
    if (!(fields >> value))
      throw std::runtime_error("Truncated process stat");
  ProcessIdentity result;
  result.pid = pid;
  result.namespace_pid = pid;
  result.start_time_ticks = std::stoull(value);
  std::istringstream status(read_file((base / "status").string(), 32768));
  std::string line;
  bool uid = false;
  while (std::getline(status, line)) {
    if (line.rfind("Uid:", 0) == 0) {
      std::istringstream values(line.substr(4));
      values >> result.uid;
      uid = true;
    }
    if (line.rfind("NSpid:", 0) == 0) {
      std::istringstream values(line.substr(6));
      int namespace_pid = 0;
      while (values >> namespace_pid) {
        if (namespace_pid <= 0)
          throw std::runtime_error("Invalid process namespace PID");
        result.namespace_pid = namespace_pid;
      }
    }
  }
  if (!uid)
    throw std::runtime_error("Process UID unavailable");
  result.executable = fs::read_symlink(base / "exe").string();
  struct stat executable {};
  if (::stat((base / "exe").c_str(), &executable) != 0)
    throw std::runtime_error("Process executable identity unavailable");
  result.executable_device = executable.st_dev;
  result.executable_inode = executable.st_ino;
  result.pid_namespace = fs::read_symlink(base / "ns/pid").string();
  return result;
}
bool same_identity(const ProcessIdentity &a, const ProcessIdentity &b) {
  return a.pid == b.pid && a.uid == b.uid &&
         a.namespace_pid == b.namespace_pid &&
         a.start_time_ticks == b.start_time_ticks &&
         a.pid_namespace == b.pid_namespace && a.executable == b.executable &&
         (!a.executable_inode || (a.executable_device == b.executable_device &&
                                  a.executable_inode == b.executable_inode));
}
void validate_identity(const Target &t, const ProcessIdentity &actual,
                       const TargetPolicy &policy) {
  validate_target(t);
  if (t.type == "host") {
    if (!policy.allow_host_resources)
      throw std::invalid_argument("Host resources not authorized");
    return;
  }
  if (t.type == "container" && !policy.allow_containers)
    throw std::invalid_argument("Container scope not authorized");
  if (t.type == "container" &&
      !policy.allowed_container_ids.contains(t.container_id))
    throw std::invalid_argument("Container ID not authorized by Agent policy");
  if (t.type == "process" && !policy.allow_host_processes &&
      actual.pid != policy.controlled_pid)
    throw std::invalid_argument("Host process scope not authorized");
  if (actual.pid <= 1 || (t.pid > 1 && actual.pid != t.pid))
    throw std::invalid_argument("Resolved PID mismatch");
  if (t.start_time_ticks && actual.start_time_ticks != t.start_time_ticks)
    throw std::invalid_argument("Target PID was reused");
  if (t.check_uid && t.uid != actual.uid)
    throw std::invalid_argument("Expected target UID mismatch");
  if (!policy.allowed_uids.contains(actual.uid))
    throw std::invalid_argument("Target UID not authorized");
}
TargetPolicy target_policy(int controlled) {
  TargetPolicy p;
  p.controlled_pid = controlled;
  p.allow_host_processes = secret("ALLOW_HOST_PROCESSES", "false") == "true";
  p.allow_containers = secret("ALLOW_CONTAINERS", "false") == "true";
  p.allow_host_resources = secret("ALLOW_HOST_RESOURCES", "false") == "true";
  std::istringstream input(
      secret("ALLOWED_TARGET_UIDS", std::to_string(getuid())));
  std::string uid;
  while (std::getline(input, uid, ',')) {
    if (uid.empty() || uid.find_first_not_of("0123456789") != std::string::npos)
      throw std::invalid_argument("ALLOWED_TARGET_UIDS must be numeric");
    p.allowed_uids.insert(static_cast<unsigned int>(std::stoul(uid)));
  }
  std::istringstream containers(secret("ALLOWED_CONTAINER_IDS"));
  std::string id;
  while (std::getline(containers, id, ',')) {
    Target target;
    target.type = "container";
    target.container_id = id;
    validate_target(target);
    p.allowed_container_ids.insert(id);
  }
  return p;
}
json target_policy_json(const TargetPolicy &policy) {
  return {{"host_processes", policy.allow_host_processes},
          {"containers", policy.allow_containers},
          {"host_resources", policy.allow_host_resources},
          {"allowed_uids", policy.allowed_uids},
          {"allowed_container_ids", policy.allowed_container_ids}};
}
ProcessIdentity resolve_target(const Target &t, const TargetPolicy &p,
                               const std::string &scratch) {
  validate_target(t);
  if (t.type == "host") {
    validate_identity(t, {}, p);
    return {};
  }
  int pid = t.pid;
  ProcessIdentity container_init;
  if (t.type == "container") {
    if (!p.allow_containers)
      throw std::invalid_argument("Container scope not authorized");
    if (!p.allowed_container_ids.contains(t.container_id))
      throw std::invalid_argument(
          "Container ID not authorized by Agent policy");
    std::vector<std::string> argv;
    if (t.runtime == "docker")
      argv = {secret("DOCKER_EXECUTABLE", "/usr/bin/docker"), "inspect",
              "--format", "{{.State.Pid}}", t.container_id};
    else
      argv = {secret("CTR_EXECUTABLE", "/usr/bin/ctr"), "-n", "k8s.io", "tasks",
              "list"};
    argv[0] = fs::canonical(argv[0]).string();
    const auto name = t.runtime == "docker" ? "docker" : "ctr";
    auto expected =
        secret(t.runtime == "docker" ? "DOCKER_SHA256" : "CTR_SHA256");
    const auto manifest =
        secret("TOOL_MANIFEST_FILE", "/opt/drop-tools/manifest.json");
    if (expected.empty() && fs::is_regular_file(manifest)) {
      auto entries = parse_json(read_file(manifest));
      if (entries.at("tools").contains(name))
        expected = entries.at("tools").at(name).at("sha256").get<std::string>();
    }
    if (expected.empty() || sha256_file(argv[0]) != expected)
      throw std::runtime_error(
          "Runtime executable lacks a matching trusted hash");
    auto result =
        execute(argv, scratch + "/runtime.log", std::chrono::seconds(5));
    if (result.exit_code != 0)
      throw std::runtime_error("Container runtime resolution failed");
    if (t.runtime == "docker") {
      std::istringstream value(result.output);
      value >> pid;
    } else {
      std::istringstream rows(result.output);
      std::string line;
      pid = 0;
      while (std::getline(rows, line)) {
        std::istringstream row(line);
        std::string id;
        int candidate = 0;
        if (row >> id >> candidate && id == t.container_id) {
          pid = candidate;
          break;
        }
      }
    }
    auto init = process_identity(pid);
    container_init = init;
    const auto container_cgroup =
        read_file("/proc/" + std::to_string(pid) + "/cgroup", 65536);
    if (t.container_pid > 1) {
      pid = 0;
      for (const auto &entry : fs::directory_iterator("/proc")) {
        const auto id = entry.path().filename().string();
        if (id.empty() ||
            id.find_first_not_of("0123456789") != std::string::npos)
          continue;
        try {
          auto identity = process_identity(std::stoi(id));
          if (identity.pid_namespace != init.pid_namespace)
            continue;
          if (read_file((entry.path() / "cgroup").string(), 65536) !=
              container_cgroup)
            continue;
          std::istringstream lines(
              read_file((entry.path() / "status").string(), 32768));
          std::string line;
          while (std::getline(lines, line))
            if (line.rfind("NSpid:", 0) == 0) {
              std::istringstream ids(line.substr(6));
              int inner = 0;
              while (ids >> inner) {
              }
              if (inner == t.container_pid) {
                pid = identity.pid;
                break;
              }
            }
          if (pid)
            break;
        } catch (const std::exception &) {
        }
      }
      if (!pid)
        throw std::runtime_error(
            "Container PID not visible in the selected namespace");
    }
  }
  auto actual = process_identity(pid);
  if (t.type == "container") {
    if (t.pid > 1 && t.pid != pid)
      throw std::runtime_error("Container host PID changed");
    if (!same_identity(container_init, process_identity(container_init.pid)))
      throw std::runtime_error("Container init process identity changed");
  }
  Target pinned = t;
  if (t.type == "container")
    pinned.pid = pid;
  validate_identity(pinned, actual, p);
  return actual;
}
} // namespace dropcore
