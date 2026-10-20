// TaskKind目录的只读访问接口，关联参数schema、原始格式和分析pipeline。
// 所有服务都应遵循contracts/task-kinds.json，不在浏览器、API或Agent各复制一套规则。

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
} // 结束 dropcore 命名空间
