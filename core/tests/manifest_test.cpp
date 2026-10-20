#include "drop/manifest.hpp"
#include <iostream>
#include <stdexcept>

int main() {
  using namespace dropcore;
  CollectionManifest input;
  input.task_id = 1;
  input.attempt_id = 2;
  input.kind = "PROC_CPU";
  input.agent_id = "fixture";
  input.agent_version = "2.0.0";
  input.build_commit = "fixture";
  input.started_ms = 10;
  input.finished_ms = 20;
  input.uploaded_ms = 30;
  input.parameters = {{"pid", 7},
                      {"duration_seconds", 1},
                      {"private_option", "must-not-survive"},
                      {"control_path", "/private/socket"},
                      {"scope", "process"}};
  input.tools = {{{"name", "perf"},
                  {"version", "perf version 6.8\nprivate-extra"},
                  {"sha256", std::string(64, 'b')},
                  {"path", "/private/tool"}}};
  input.files = {{{"kind", "RAW"},
                  {"name", "profile.folded"},
                  {"object_key", "raw/tasks/1/attempts/1/profile.folded"},
                  {"size", 47},
                  {"sha256", std::string(64, 'a')},
                  {"etag", "\"opaque-receipt\""},
                  {"put_url", "https://fixture.invalid/token"}}};
  const auto encoded = build_collection_manifest(input);
  const auto parsed = nlohmann::json::parse(encoded);
  if (encoded != build_collection_manifest(input) ||
      encoded.find("private") != std::string::npos ||
      encoded.find("put_url") != std::string::npos ||
      parsed["files"][0]["etag"] != "\"opaque-receipt\"" ||
      parsed["collection_finished_unix_ms"] != 20)
    throw std::runtime_error(
        "Manifest allowlist, determinism, receipt or timing failed");
  for (int test = 0; test < 5; ++test) {
    auto bad = input;
    if (test == 0)
      bad.uploaded_ms = 19;
    if (test == 1)
      bad.files[0]["kind"] = "MANIFEST";
    if (test == 2)
      bad.files[0]["sha256"] = "fake";
    if (test == 3)
      bad.files[0]["object_key"] = "https://fixture.invalid/token";
    if (test == 4)
      bad.files.push_back(bad.files[0]);
    bool rejected = false;
    try {
      build_collection_manifest(bad);
    } catch (const std::invalid_argument &) {
      rejected = true;
    }
    if (!rejected)
      throw std::runtime_error("Invalid manifest evidence accepted");
  }
  std::cout << "manifest allowlist, timing, integrity and deterministic bytes "
               "passed\n";
}
