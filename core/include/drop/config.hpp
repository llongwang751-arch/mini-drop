#pragma once
#include <functional>
#include <map>
#include <mutex>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <vector>

namespace dropcore {
using EnvironmentLookup =
    std::function<std::optional<std::string>(const std::string &)>;
class Configuration {
public:
  explicit Configuration(const std::vector<std::string> &arguments = {},
                         EnvironmentLookup environment = {});
  std::string get(const std::string &name,
                  const std::string &fallback = "") const;
  nlohmann::json snapshot() const;

private:
  std::optional<std::string> environment(const std::string &) const;
  std::map<std::string, std::string> cli_, file_;
  EnvironmentLookup environment_;
  mutable std::mutex mutex_;
  mutable nlohmann::json values_ = nlohmann::json::object(),
                         sources_ = nlohmann::json::object();
};
void initialize_configuration(int argc, char **argv);
std::string configuration_value(const std::string &name,
                                const std::string &fallback = "");
nlohmann::json configuration_snapshot();
bool sensitive_configuration_key(const std::string &name);
} // namespace dropcore
