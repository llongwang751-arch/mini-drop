#pragma once
#include <map>
#include <nlohmann/json.hpp>
#include <set>
#include <stdexcept>
#include <string>

namespace dropcore {
inline nlohmann::json parse_json(const std::string &source,
                                 std::size_t maximum = 512 * 1024) {
  using json = nlohmann::json;
  if (source.size() > maximum)
    throw std::invalid_argument("JSON document exceeds byte budget");
  std::map<int, std::set<std::string>> keys;
  auto callback = [&](int depth, json::parse_event_t event, json &value) {
    if (depth > 32)
      throw std::invalid_argument("JSON nesting exceeds budget");
    if (event == json::parse_event_t::object_start)
      keys[depth + 1].clear();
    if (event == json::parse_event_t::object_end)
      keys.erase(depth + 1);
    if (event == json::parse_event_t::key) {
      const auto key = value.get<std::string>();
      if (key.size() > 256 || !keys[depth].insert(key).second)
        throw std::invalid_argument("JSON key exceeds budget or is duplicated");
    }
    return true;
  };
  try {
    return json::parse(source, callback);
  } catch (const json::exception &) {
    throw std::invalid_argument("Invalid JSON document");
  }
}
} // namespace dropcore
