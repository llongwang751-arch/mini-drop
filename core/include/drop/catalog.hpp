#pragma once
#include <map>
#include <nlohmann/json.hpp>
#include <string>
namespace dropcore {
class Catalog {
public:
  explicit Catalog(const std::string &path);
  const nlohmann::json &get(const std::string &kind) const;
  void validate(const std::string &kind,
                const nlohmann::json &parameters) const;
  const std::map<std::string, nlohmann::json> &entries() const {
    return entries_;
  }

private:
  std::map<std::string, nlohmann::json> entries_;
};
void validate_schema(const nlohmann::json &schema, const nlohmann::json &value,
                     unsigned int depth = 0);
} // namespace dropcore
