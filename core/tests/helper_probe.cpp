#include "drop/artifact.hpp"
#include "drop/helper.hpp"
#include "drop/json.hpp"
#include <atomic>
#include <csignal>
#include <curl/curl.h>
#include <iostream>
#include <sys/stat.h>
#include <thread>
namespace {
std::atomic<bool> canceled{false};
void stop(int) { canceled = true; }
} // namespace
int main(int argc, char **argv) {
  using namespace dropcore;
  using json = nlohmann::json;
  if (argc != 5)
    return 2;
  std::signal(SIGTERM, stop);
  std::signal(SIGINT, stop);
  curl_global_init(CURL_GLOBAL_DEFAULT);
  try {
    Catalog catalog(argv[1]);
    HelperClient client(argv[2], catalog);
    const auto input = parse_json(read_file(argv[3], 65536));
    const auto operation = input.value("op", "collect");
    if (operation == "capabilities") {
      std::cout << client.capabilities().dump() << '\n';
      return 0;
    }
    if (operation == "ack") {
      client.acknowledge(input.at("token"));
      std::cout << "{\"ok\":true}\n";
      return 0;
    }
    HelperRequest request;
    request.token = input.at("token");
    auto &context = request.context;
    context.kind = input.at("kind");
    context.parameters = input.at("parameters");
    context.options = context.parameters.value("options", json::object());
    if (operation == "resume" && input.contains("saved_request"))
      request = decode_helper_request(
          parse_json(read_file(input.at("saved_request"), 65536)), catalog);
    else
      context.target =
          process_identity(target_from_json(context.parameters).pid);
    context.directory = argv[4];
    std::filesystem::create_directories(context.directory);
    chmod(argv[4], 0700);
    context.duration_seconds = context.parameters.value("duration_seconds", 1);
    context.frequency_hz = context.parameters.value(
        "frequency_hz",
        catalog.get(context.kind).value("default_frequency_hz", 99));
    context.max_file_bytes = catalog.get(context.kind).at("max_file_bytes");
    context.limits.file_bytes = context.max_file_bytes;
    context.protocol_version = input.value("collection_protocol", 2);
    context.canceled = [] { return canceled.load(); };
    request.controlled_pid = input.value("controlled_pid", 0);
    request.binary_max = input.value("binary_max", std::uint64_t{0});
    if (input.value("tamper_start_time", false))
      ++context.target.start_time_ticks;
    if (operation != "resume")
      atomic_write((context.directory / "helper-request.json").string(),
                   encode_helper_request(request).dump());
    std::jthread cancellation;
    if (input.contains("cancel_after_ms"))
      cancellation = std::jthread([milliseconds =
                                       input.at("cancel_after_ms").get<int>()] {
        std::this_thread::sleep_for(std::chrono::milliseconds(milliseconds));
        canceled = true;
      });
    const auto output = client.collect(request, operation == "resume");
    std::cout << json{{"ok", true},
                      {"kind", context.kind},
                      {"name", output.name},
                      {"format", output.format},
                      {"size", std::filesystem::file_size(output.path)},
                      {"sha256", sha256_file(output.path.string())},
                      {"metadata", output.metadata}}
                     .dump()
              << '\n';
    return 0;
  } catch (const std::exception &error) {
    const auto *failure = dynamic_cast<const HelperFailure *>(&error);
    std::cout << json{{"ok", false},
                      {"error_code",
                       failure ? failure->error_code : "PROBE_FAILED"}}
                     .dump()
              << '\n';
    return 1;
  }
}
