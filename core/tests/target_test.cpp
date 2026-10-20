#include "drop/process.hpp"
#include "drop/target.hpp"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <unistd.h>
template <class F> void rejects(F &&f) {
  try {
    f();
  } catch (const std::exception &) {
    return;
  }
  throw std::runtime_error("Unsafe target accepted");
}
int main(int argc, char **argv) {
  using namespace dropcore;
  Target t;
  t.pid = 123;
  t.start_time_ticks = 90;
  TargetPolicy p;
  p.allow_host_processes = true;
  p.allowed_uids = {10001};
  ProcessIdentity a{123, 10001, 90, "/bin/target", "pid:[1]"};
  validate_target(t);
  validate_identity(t, a, p);
  rejects([&] {
    auto bad = t;
    bad.pid = 1;
    validate_target(bad);
  });
  rejects([&] {
    auto bad = t;
    bad.container_id = "$(touch /tmp/pwn)";
    bad.type = "container";
    validate_target(bad);
  });
  rejects([&] {
    auto bad = a;
    bad.start_time_ticks = 91;
    validate_identity(t, bad, p);
  });
  rejects([&] {
    auto bad = a;
    bad.uid = 0;
    validate_identity(t, bad, p);
  });
  rejects([&] {
    auto policy = p;
    policy.allow_host_processes = false;
    validate_identity(t, a, policy);
  });
  rejects([&] {
    auto bad = t;
    bad.type = "container";
    bad.container_id = std::string(64, 'a');
    validate_identity(bad, a, p);
  });
  auto self = process_identity(getpid());
  {
    const auto fixture = std::filesystem::current_path() /
                         ("target-fixture-" + std::to_string(getpid()));
    if (!std::filesystem::create_directory(fixture))
      throw std::runtime_error("Target fixture directory already exists");
    const auto process = fixture / "111";
    std::filesystem::create_directories(process / "ns");
    std::filesystem::create_symlink("/proc/self/exe", process / "exe");
    std::filesystem::create_symlink("pid:[4026533838]", process / "ns/pid");
    std::ofstream(process / "stat") << read_file("/proc/self/stat");
    std::ofstream(process / "status") << "Uid:\t10001\t10001\t10001\t10001\n"
                                         "NSpid:\t111\t7\n";
    const auto nested_identity = process_identity(111, fixture);
    if (nested_identity.pid != 111 || nested_identity.namespace_pid != 7 ||
        nested_identity.pid_namespace != "pid:[4026533838]")
      throw std::runtime_error("Innermost PID namespace identity was lost");
    std::ofstream(process / "status") << "Uid:\t10001\t10001\t10001\t10001\n"
                                         "NSpid:\t111\t0\n";
    rejects([&] { process_identity(111, fixture); });
    std::filesystem::remove_all(fixture);
  }
  if (argc != 2)
    throw std::runtime_error("Shared target policy fixture required");
  {
    TargetPolicy policy;
    policy.allow_host_processes = true;
    policy.allow_containers = true;
    policy.allow_host_resources = true;
    policy.allowed_uids = {10001};
    policy.allowed_container_ids = {std::string(64, 'a')};
    if (target_policy_json(policy) !=
        nlohmann::json::parse(dropcore::read_file(argv[1])))
      throw std::runtime_error(
          "Agent target policy differs from shared API JSON contract");
  }
  {
    auto container = t;
    container.type = "container";
    container.container_id = std::string(64, 'a');
    auto policy = p;
    policy.allow_containers = true;
    rejects([&] { validate_identity(container, a, policy); });
    policy.allowed_container_ids.insert(container.container_id);
    validate_identity(container, a, policy);
  }
  if (self.pid != getpid() || !self.start_time_ticks ||
      self.namespace_pid != getpid() || self.executable.empty() ||
      !same_identity(self, process_identity(getpid())))
    throw std::runtime_error("Live proc identity parse failed");
  auto nested = target_from_json({{"pid", 123},
                                  {"target",
                                   {{"type", "container"},
                                    {"container_id", std::string(64, 'a')},
                                    {"container_pid", 5}}}});
  if (nested.type != "container" || nested.container_pid != 5)
    throw std::runtime_error("Nested target lost");
  std::cout << "target: process reuse, UID, scope, container and live identity "
               "checks passed\n";
}
