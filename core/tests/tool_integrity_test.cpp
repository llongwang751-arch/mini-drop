#include "drop/artifact.hpp"
#include "drop/runner.hpp"
#include <chrono>
#include <fstream>
#include <iostream>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
int main(int argc, char **argv) {
  using namespace dropcore;
  if (std::filesystem::path(argv[0]).filename() == "slow-bpftrace" ||
      std::filesystem::path(argv[0]).filename() == "slow-memray") {
    std::this_thread::sleep_for(std::chrono::seconds(6));
    std::cout << "Imported slow tool fixture, not real bpftrace\n";
    return 0;
  }
  if (std::filesystem::path(argv[0]).filename() == "asprof-alias") {
    if (argc != 2)
      return 3;
    std::cout << "fixture multicall mode\n";
    return 0;
  }
  if (argc != 2)
    return 2;
  const auto directory = std::filesystem::current_path() /
                         ("tool-fixture-" + std::to_string(getpid()));
  std::filesystem::create_directory(directory);
  chmod(directory.c_str(), 0700);
  const auto alias = directory / "asprof-alias";
  std::filesystem::create_symlink(std::filesystem::canonical("/proc/self/exe"),
                                  alias);
  setenv("ASPROF_EXECUTABLE", alias.c_str(), 1);
  setenv("ASPROF_SHA256", sha256_file(alias.string()).c_str(), 1);
  Catalog alias_catalog(argv[1]);
  RunnerRegistry alias_registry(alias_catalog, directory.string(),
                                "/missing/perf");
  if (!alias_registry.available("JAVA_CPU") ||
      alias_registry.tool("asprof").path != alias.string())
    throw std::runtime_error("Multicall tool argv0 alias was lost");
  const auto tool = directory / "asprof";
  std::filesystem::copy_file("/usr/bin/true", tool);
  chmod(tool.c_str(), 0500);
  setenv("ASPROF_EXECUTABLE", tool.c_str(), 1);
  const auto expected = sha256_file(tool.string());
  setenv("ASPROF_SHA256", expected.c_str(), 1);
  Catalog catalog(argv[1]);
  RunnerRegistry registry(catalog, directory.string(), "/missing/perf");
  if (!registry.available("JAVA_CPU"))
    throw std::runtime_error("Pinned fixture tool probe failed");
  chmod(tool.c_str(), 0600);
  std::ofstream changed(tool, std::ios::app);
  changed << "tampered";
  changed.close();
  chmod(tool.c_str(), 0500);
  RunnerContext c;
  c.kind = "JAVA_CPU";
  c.target = process_identity(getpid());
  c.parameters = {{"pid", getpid()}};
  c.directory = directory;
  bool rejected = false;
  try {
    registry.collect(c);
  } catch (const std::runtime_error &error) {
    rejected = std::string(error.what()) == "TOOL_INTEGRITY_CHANGED";
  }
  if (!rejected)
    throw std::runtime_error(
        "Modified executable was not rejected before Start");
  const auto slow = directory / "slow-bpftrace";
  std::filesystem::copy_file("/proc/self/exe", slow);
  chmod(slow.c_str(), 0500);
  setenv("BPFTRACE_EXECUTABLE", slow.c_str(), 1);
  setenv("BPFTRACE_SHA256", sha256_file(slow.string()).c_str(), 1);
  RunnerRegistry cold_registry(catalog, directory.string(), "/missing/perf");
  if (!cold_registry.tool("bpftrace").available ||
      cold_registry.tool("bpftrace")
              .version.find("Imported slow tool fixture") == std::string::npos)
    throw std::runtime_error(
        "Bounded cold-start probe rejected a healthy heavy tool");
  setenv("BPFTRACE_EXECUTABLE", "/missing/fixture-bpftrace", 1);
  const auto slow_memray = directory / "slow-memray";
  std::filesystem::copy_file("/proc/self/exe", slow_memray);
  chmod(slow_memray.c_str(), 0500);
  setenv("MEMRAY_EXECUTABLE", slow_memray.c_str(), 1);
  setenv("MEMRAY_SHA256", sha256_file(slow_memray.string()).c_str(), 1);
  RunnerRegistry python_cold_registry(catalog, directory.string(),
                                      "/missing/perf");
  if (!python_cold_registry.tool("memray").available ||
      python_cold_registry.tool("memray").version.find(
          "Imported slow tool fixture") == std::string::npos)
    throw std::runtime_error(
        "Bounded cold Python import probe rejected a healthy pinned tool");
  std::filesystem::remove_all(directory);
  std::cout << "tool integrity: pinned version probe and hash drift rejected "
               "before execution\n";
}
