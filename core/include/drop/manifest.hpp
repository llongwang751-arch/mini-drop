#pragma once
#include <cstdint>
#include <nlohmann/json.hpp>
#include <string>

namespace dropcore {
struct CollectionManifest {
  std::int64_t task_id = 0, attempt_id = 0;
  std::string kind, agent_id, agent_version, build_commit;
  std::int64_t started_ms = 0, finished_ms = 0, uploaded_ms = 0;
  nlohmann::json parameters = nlohmann::json::object();
  nlohmann::json tools = nlohmann::json::array();
  nlohmann::json files = nlohmann::json::array();
};
// Explicit allowlists exclude application paths, credentials and raw metadata.
std::string build_collection_manifest(const CollectionManifest &);
} // namespace dropcore
