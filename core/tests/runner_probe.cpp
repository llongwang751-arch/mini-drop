#include "drop/artifact.hpp"
#include "drop/runner.hpp"
#include <charconv>
#include <climits>
#include <curl/curl.h>
#include <iostream>
#include <sys/stat.h>

int main(int argc, char **argv) {
  using namespace dropcore;
  using json = nlohmann::json;
  if (argc == 3 && std::string(argv[1]) == "--allocator-argv") {
    try {
      const std::string number = argv[2];
      int pid = 0;
      const auto converted =
          std::from_chars(number.data(), number.data() + number.size(), pid);
      if (number.empty() || number.front() == '0' ||
          number.find_first_not_of("0123456789") != std::string::npos ||
          converted.ec != std::errc{} ||
          converted.ptr != number.data() + number.size() || pid <= 1)
        return 2;
      RunnerContext context;
      context.kind = "MEMORY_TRACE";
      context.target = process_identity(pid);
      if (context.target.uid != 10001)
        return 2;
      context.duration_seconds = 2;
      context.frequency_hz = 99;
      context.options = {
          {"category", "alloc"}, {"max_events", 10000}, {"stack_depth", 16}};
      std::cout << json(build_runner_argv(context,
                                          {{"bpftrace", "/usr/bin/bpftrace"}}))
                       .dump()
                << '\n';
      return 0;
    } catch (...) {
      return 2;
    }
  }
  if (argc != 4) {
    std::cerr << "usage: drop_runner_probe catalog.json request.json "
                 "private-output-directory\n";
    return 2;
  }
  curl_global_init(CURL_GLOBAL_DEFAULT);
  try {
    Catalog catalog(argv[1]);
    const auto request = json::parse(read_file(argv[2], 65536));
    const auto kind = request.at("kind").get<std::string>();
    const auto parameters = request.at("parameters");
    catalog.validate(kind, parameters);
    std::filesystem::create_directories(argv[3]);
    chmod(argv[3], 0700);
    RunnerRegistry registry(catalog, argv[3],
                            secret("PERF_EXECUTABLE", "/usr/bin/perf"));
    RunnerContext context;
    context.kind = kind;
    context.parameters = parameters;
    context.options = parameters.value("options", json::object());
    context.directory = argv[3];
    context.duration_seconds = parameters.value("duration_seconds", 1);
    context.frequency_hz = parameters.value(
        "frequency_hz", catalog.get(kind).value("default_frequency_hz", 99));
    context.max_file_bytes = catalog.get(kind).at("max_file_bytes");
    context.limits.file_bytes = context.max_file_bytes;
    context.target =
        resolve_target(target_from_json(parameters), target_policy(0), argv[3]);
    auto output = registry.collect(context);
    std::cout << json{{"kind", kind},
                      {"name", output.name},
                      {"format", output.format},
                      {"content_type", output.content_type},
                      {"size", std::filesystem::file_size(output.path)},
                      {"sha256", sha256_file(output.path.string())},
                      {"metadata", output.metadata}}
                     .dump(2)
              << '\n';
    curl_global_cleanup();
    return 0;
  } catch (const std::exception &error) {
    const auto *execution =
        dynamic_cast<const ProfilerExecutionFailure *>(&error);
    std::cerr << json{{"success", false},
                      {"error", error.what()},
                      {"error_code",
                       execution ? execution->error_code : "RUNNER_FAILED"}}
                     .dump()
              << '\n';
    curl_global_cleanup();
    return 1;
  }
}
