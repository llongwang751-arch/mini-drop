#include "drop/artifact.hpp"
#include "drop/config.hpp"
#include "drop/process.hpp"
#include <filesystem>
#include <iostream>
#include <limits>
#include <map>
#include <regex>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

using namespace dropcore;
using json = nlohmann::json;
template <class F> void rejects(F action) {
  try {
    action();
  } catch (const std::exception &) {
    return;
  }
  throw std::runtime_error("Unsafe configuration accepted");
}
void require(bool condition, const char *reason) {
  if (!condition)
    throw std::runtime_error(reason);
}
EnvironmentLookup environment(std::map<std::string, std::string> values = {}) {
  return [values = std::move(values)](
             const std::string &name) -> std::optional<std::string> {
    if (const auto found = values.find(name); found != values.end())
      return found->second;
    return std::nullopt;
  };
}
int main() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("drop-config-test-" + std::to_string(getpid()));
  std::filesystem::create_directory(root);
  chmod(root.c_str(), 0700);
  const auto file = root / "config.json", secret_file = root / "credential",
             first_ref = root / "first", second_ref = root / "second";
  atomic_write(
      file.string(),
      R"({"AGENT_ID":"file-agent","RUN_CONTROLLED_WORKLOAD":true,"RAW_RECOVERY_DISK_BYTES":1073741824})");
  atomic_write(secret_file.string(),
               "postgresql://user:imported-fixture-secret@db/drop\n");
  atomic_write(first_ref.string(), "first-file\n");
  atomic_write(second_ref.string(), "second-file\r\n");
  Configuration all({"--config", file.string(), "--set", "AGENT_ID=cli-agent"},
                    environment({{"AGENT_ID", "env-agent"}}));
  require(all.get("AGENT_ID", "default-agent") == "cli-agent",
          "CLI did not precede environment/file/default");
  Configuration env({"--config", file.string()},
                    environment({{"AGENT_ID", "env-agent"}}));
  require(env.get("AGENT_ID", "default-agent") == "env-agent",
          "Environment did not precede file/default");
  Configuration configured({"--config", file.string()}, environment());
  require(configured.get("AGENT_ID", "default-agent") == "file-agent" &&
              configured.get("RUN_CONTROLLED_WORKLOAD") == "true" &&
              configured.get("RAW_RECOVERY_DISK_BYTES") == "1073741824",
          "Flat JSON typed values changed");
  Configuration defaults({}, environment());
  require(defaults.get("AGENT_ID", "default-agent") == "default-agent",
          "Safe caller default lost");
  Configuration file_env({},
                         environment({{"AGENT_ID", "direct"},
                                      {"AGENT_ID_FILE", first_ref.string()}}));
  require(file_env.get("AGENT_ID") == "first-file",
          "Legacy ENV_FILE did not precede ENV in the same tier");
  Configuration cli_over_file(
      {"--set", "AGENT_ID=cli-agent"},
      environment({{"AGENT_ID_FILE", first_ref.string()}}));
  require(cli_over_file.get("AGENT_ID") == "cli-agent",
          "ENV_FILE incorrectly bypassed higher-priority CLI");
  Configuration cli_ref({"--set", "AGENT_ID_FILE=" + second_ref.string()},
                        environment({{"AGENT_ID_FILE", first_ref.string()}}));
  require(cli_ref.get("AGENT_ID") == "second-file",
          "CLI file reference or CRLF trimming changed");

  Configuration secret(
      {}, environment({{"DATABASE_URL_FILE", secret_file.string()},
                       {"TLS_KEY_FILE", "/private/tls.key"},
                       {"OTEL_EXPORTER_OTLP_ENDPOINT",
                        "https://user:imported-url-secret@collector:4318/"
                        "private-token?sig=imported-query-secret"}}));
  require(secret.get("DATABASE_URL").find("imported-fixture-secret") !=
              std::string::npos,
          "Secret file was not resolved");
  const auto snapshot = secret.snapshot();
  const auto rendered = snapshot.dump();
  for (const auto *needle : {"imported-fixture-secret", "imported-url-secret",
                             "imported-query-secret", "private-token",
                             "/private/tls.key", "postgresql://"})
    require(rendered.find(needle) == std::string::npos,
            "Secret, URL credential, path token or private-key reference "
            "entered startup snapshot");
  require(snapshot.at("values").at("OTEL_EXPORTER_OTLP_ENDPOINT") ==
              "https://collector:4318/[redacted]",
          "Public endpoint routing evidence was lost");
  atomic_write(secret_file.string(),
               "postgresql://user:different-imported-secret@db/drop\n");
  require(snapshot.at("sha256") == secret.snapshot().at("sha256"),
          "Secret plaintext entered the configuration hash oracle");
  Configuration same_a({"--set", "AGENT_ID=unchanged"}, environment());
  Configuration same_b({}, environment({{"AGENT_ID", "unchanged"}}));
  require(same_a.snapshot().at("sha256") == same_b.snapshot().at("sha256"),
          "Identical effective values produced unstable hash across sources");
  Configuration different({"--set", "AGENT_ID=changed"}, environment());
  require(same_a.snapshot().at("sha256") != different.snapshot().at("sha256"),
          "Public configuration changes were not fingerprinted");
  chmod(secret_file.c_str(), 0444);
  require(!secret.get("DATABASE_URL").empty(),
          "Protected read-only Docker secret mount lost compatibility");

  for (const auto &arguments : std::vector<std::vector<std::string>>{
           {"--unknown"},
           {"--set"},
           {"--config", "relative.json"},
           {"--set", "UNKNOWN_VALUE=x"},
           {"--set", "DATABASE_URL=postgresql://imported-secret"},
           {"--set", "AGENT_ID=one", "--set", "AGENT_ID=two"},
           {"--set", "AGENT_ID=line\nbreak"},
           {"--set", "AGENT_ID=\xC2\x85"},
           {"--set", "AGENT_ID=\xE2\x80\xAE"},
           {"--set", "AGENT_ID=" + std::string(4097, 'x')},
           {"--set", "TLS_KEY_FILE=relative.key"}})
    rejects([&] { Configuration invalid(arguments, environment()); });
  rejects([&] {
    Configuration invalid({"--set", "COLLECTOR_HELPER_REQUIRE_BOUNDED_FS=tru"},
                          environment());
  });
  Configuration invalid_boolean(
      {}, environment({{"COLLECTOR_HELPER_REQUIRE_BOUNDED_FS", "tru"}}));
  rejects([&] {
    (void)invalid_boolean.get("COLLECTOR_HELPER_REQUIRE_BOUNDED_FS", "true");
  });
  for (const auto *value : {"4294967296", "4294977297", "-1", "+10001", " 10001", "10001,", "10001,4294967296"})
    rejects([&] { Configuration invalid({"--set", std::string("ALLOWED_TARGET_UIDS=") + value}, environment()); });
  Configuration overflowing_uid({}, environment({{"COLLECTOR_HELPER_AGENT_UID", "4294977297"}}));
  rejects([&] { (void)overflowing_uid.get("COLLECTOR_HELPER_AGENT_UID", "10001"); });
  Configuration root_explicit({"--set", "ALLOWED_TARGET_UIDS=0,10001"}, environment());
  require(root_explicit.get("ALLOWED_TARGET_UIDS") == "0,10001", "An explicitly configured authorized UID list changed");
  for (const auto *contents : {"[]", R"({"AGENT_ID":[]})", R"({"UNKNOWN":"x"})",
                               R"({"AGENT_ID":"a","AGENT_ID":"b"})",
                               R"({"DATABASE_URL":"imported-secret"})"}) {
    atomic_write(file.string(), contents);
    rejects([&] {
      Configuration invalid({"--config", file.string()}, environment());
    });
  }
  atomic_write(file.string(), std::string(65537, 'x'));
  rejects([&] {
    Configuration invalid({"--config", file.string()}, environment());
  });
  std::filesystem::remove(file);
  if (mkfifo(file.c_str(), 0600))
    throw std::runtime_error("Cannot create FIFO configuration fixture");
  rejects([&] {
    Configuration invalid({"--config", file.string()}, environment());
  });
  std::filesystem::remove(file);
  std::filesystem::create_symlink(first_ref, file);
  rejects([&] {
    Configuration invalid({"--config", file.string()}, environment());
  });

  const auto empty_log = log_record("drop-agent", "Fixture operation complete");
  require(empty_log.size() == 12 && empty_log.at("request_id").is_null() &&
              empty_log.at("task_id").is_null() &&
              empty_log.at("duration_ms").is_null() &&
              empty_log.at("error_code").is_null(),
          "Unified log schema/default nulls changed");
  require(std::regex_match(empty_log.at("time").get<std::string>(),
                           std::regex("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-"
                                      "9]{2}:[0-9]{2}\\.[0-9]{3}Z")),
          "Log timestamp is not UTC RFC3339 milliseconds");
  LogContext context;
  context.request_id = "fixture-request";
  context.task_id = 17;
  context.attempt_id = 23;
  context.agent_id = "fixture-agent";
  context.stage = "collection";
  context.duration_ms = 12.5;
  const auto correlated =
      log_record("drop-agent", "Fixture failed\nwithout extra lines",
                 "TASK_CANCELED", context);
  require(correlated.at("request_id") == "fixture-request" &&
              correlated.at("task_id") == 17 &&
              correlated.at("attempt_id") == 23 &&
              correlated.at("agent_id") == "fixture-agent" &&
              correlated.at("stage") == "collection" &&
              correlated.at("duration_ms") == 12.5 &&
              correlated.at("message").get<std::string>().find('\n') ==
                  std::string::npos,
          "Task/attempt correlation or single-line logging was lost");
  context.request_id = "http://private?secret=fixture";
  context.duration_ms = std::numeric_limits<double>::infinity();
  const auto invalid =
      log_record("drop-helper", "Bounded failure", "TASK_CANCELED", context);
  require(invalid.at("request_id").is_null() &&
              invalid.at("duration_ms").is_null(),
          "Unsafe context entered unified logs");
  std::filesystem::remove_all(root);
  std::cout << "configuration: CLI/env/JSON/default + ENV_FILE precedence; "
               "protected bounded files; invalid inputs; secret-free stable "
               "hashes; 12-field correlated UTC logs\n";
}
