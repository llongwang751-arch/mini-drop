#include "drop/artifact.hpp"
#include "drop/helper.hpp"
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

using json = nlohmann::json;
template <class F> void rejects(F action) {
  try {
    action();
  } catch (const std::exception &) {
    return;
  }
  throw std::runtime_error("Unsafe collector helper contract was accepted");
}
int main(int argc, char **argv) {
  using namespace dropcore;
  if (argc != 2)
    return 2;
  const auto directory = std::filesystem::temp_directory_path() /
                         ("drop-helper-contract-" + std::to_string(getpid()));
  std::filesystem::create_directory(directory);
  chmod(directory.c_str(), 0700);
  if (mkfifo((directory / "result.json.tmp").c_str(), 0600))
    throw std::runtime_error("Cannot create FIFO fault fixture");
  rejects([&] { atomic_write((directory / "result.json").string(), "{}"); });
  std::filesystem::remove(directory / "result.json.tmp");
  atomic_write((directory / "original").string(), "must remain intact");
  std::filesystem::create_hard_link(directory / "original",
                                    directory / "result.json.tmp");
  rejects([&] { atomic_write((directory / "result.json").string(), "{}"); });
  if (read_file((directory / "original").string()) != "must remain intact")
    throw std::runtime_error(
        "Journal hard link was truncated before validation");
  std::filesystem::remove_all(directory);
  Catalog catalog(argv[1]);
  HelperRequest request;
  request.token = std::string(64, 'a');
  request.context.kind = "PERF_CPU";
  request.context.parameters = {
      {"pid", 4321}, {"duration_seconds", 1}, {"frequency_hz", 9}};
  request.context.target.pid = 4321;
  request.context.target.uid = 10001;
  request.context.target.start_time_ticks = 777;
  request.context.target.namespace_pid = 11;
  request.context.target.pid_namespace = "pid:[4026532756]";
  request.context.target.executable_device = 29;
  request.context.target.executable_inode = 12345;
  request.context.target.executable = "/private/target/path";
  request.context.directory = "/private/agent/path";
  request.context.output_directory = "/private/helper/receive";
  request.context.limits.cpu_affinity = {1, 3};
  const auto encoded = encode_helper_request(request);
  const auto decoded = decode_helper_request(encoded, catalog);
  if (!decoded.context.directory.empty() ||
      !decoded.context.output_directory.empty() ||
      !decoded.context.target.executable.empty() ||
      encoded.dump().find("/private") != std::string::npos ||
      decoded.context.target.executable_inode != 12345 ||
      decoded.context.limits.cpu_affinity != std::vector<unsigned>{1, 3})
    throw std::runtime_error(
        "Helper contract exposed a path or lost pinned identity/budget");
  unsigned cases = 0;
  const auto bad = [&](auto mutate) {
    auto value = encoded;
    mutate(value);
    rejects([&] { decode_helper_request(value, catalog); });
    ++cases;
  };
  for (const auto *field :
       {"put_url", "command", "argv", "directory", "credentials", "env"})
    bad([&](auto &value) {
      value[field] = "http://storage/private?signature=secret";
    });
  for (const auto *token :
       {"../cache", "",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "a/b"})
    bad([&](auto &value) { value["token"] = token; });
  bad([](auto &value) { value["op"] = "execute"; });
  bad([](auto &value) { value["protocol_version"] = 2; });
  bad([](auto &value) { value["collection_protocol"] = 0; });
  bad([](auto &value) { value["kind"] = "SCRIPT"; });
  bad([](auto &value) {
    value["parameters"]["options"] = {{"command", "touch /tmp/no"}};
  });
  bad([](auto &value) { value["max_file_bytes"] = 33554433; });
  bad([](auto &value) { value["max_file_bytes"] = 0; });
  bad([](auto &value) { value["binary_max"] = 1; });
  bad([](auto &value) { value["budget"]["cpu_seconds"] = 301; });
  bad([](auto &value) {
    value["budget"]["address_space_bytes"] = 8589934593ULL;
  });
  bad([](auto &value) { value["budget"]["processes"] = 65; });
  bad([](auto &value) { value["budget"]["cpu_affinity"] = {1, 1}; });
  bad([](auto &value) { value["budget"]["cpu_affinity"] = {-1}; });
  bad([](auto &value) { value["budget"]["cpu_affinity"] = {1024}; });
  bad([](auto &value) { value["budget"]["shell"] = "/bin/sh"; });
  bad([](auto &value) { value["target_identity"]["pid"] = 1; });
  bad([](auto &value) {
    value["target_identity"]["pid_namespace"] = "../../host";
  });
  bad([](auto &value) { value["target_identity"]["executable_inode"] = 0; });
  bad([](auto &value) { value["target_identity"]["executable"] = "/bin/sh"; });
  for (const auto *kind : {"JAVA_HEAP", "PYTHON_MEMORY", "BOLT_PROFILE"}) {
    const auto raw =
        catalog.get(kind).at("max_file_bytes").get<std::uint64_t>();
    const auto binary =
        std::string(kind) == "BOLT_PROFILE" ? 512ULL * 1024 * 1024 : 0;
    const auto required = 4 * raw + binary + 1024 * 1024;
    if (required > 2ULL * 1024 * 1024 * 1024 ||
        required <= 1024ULL * 1024 * 1024)
      throw std::runtime_error(
          "Default helper budget no longer covers full catalog defaults");
  }
  if (helper_kind("JAVA_CPU") || helper_kind("JAVA_HEAP") ||
      !helper_kind("PYTHON_MEMORY") || helper_kind("GPERF_CPU") ||
      helper_kind("PROC_CPU") || helper_kind("SCRIPT"))
    throw std::runtime_error("Helper collection boundary changed");
  std::cout << "helper typed contract: " << cases
            << " malicious request/budget/identity cases rejected; no "
               "paths/URLs/argv encoded\n";
}
