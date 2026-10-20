// 读取TaskKind真源目录，统一任务类型、参数schema、制品格式与分析入口。
// 目录是控制面与采集端的共同约定；Runner额外报告本机是否具备实际工具与权限。
// 因此“目录中存在该类型”和“这个节点当前可采该类型”需要分开判断。

#include "drop/catalog.hpp"
#include "drop/json.hpp"
#include "drop/process.hpp"
#include <regex>
#include <stdexcept>
using json = nlohmann::json;
namespace dropcore {
void validate_schema(const json &s, const json &v, unsigned int depth) {
  if (depth > 16)
    throw std::invalid_argument("Parameter nesting exceeds budget");
  const auto type = s.value("type", "");
  if ((type == "object" && !v.is_object()) ||
      (type == "array" && !v.is_array()) ||
      (type == "integer" && !v.is_number_integer()) ||
      (type == "string" && !v.is_string()) ||
      (type == "boolean" && !v.is_boolean()))
    throw std::invalid_argument("Parameter type mismatch");
  if (s.contains("enum") &&
      std::find(s["enum"].begin(), s["enum"].end(), v) == s["enum"].end())
    throw std::invalid_argument("Parameter enum mismatch");
  if (v.is_number() &&
      ((s.contains("minimum") &&
        v.get<double>() < s["minimum"].get<double>()) ||
       (s.contains("maximum") && v.get<double>() > s["maximum"].get<double>())))
    throw std::invalid_argument("Parameter outside allowed range");
  if (v.is_string()) {
    const auto text = v.get<std::string>();
    if (text.size() > 4096 || (s.contains("maxLength") &&
                               text.size() > s["maxLength"].get<std::size_t>()))
      throw std::invalid_argument("Parameter string too long");
    if (text.find('\0') != std::string::npos)
      throw std::invalid_argument("NUL in parameter");
    if (s.contains("pattern") &&
        !std::regex_match(text, std::regex(s["pattern"].get<std::string>())))
      throw std::invalid_argument("Parameter pattern mismatch");
  }
  if (v.is_object()) {
    if (s.contains("maxProperties") &&
        v.size() > s["maxProperties"].get<std::size_t>())
      throw std::invalid_argument("Too many parameter object entries");
    auto properties = s.value("properties", json::object());
    for (const auto &key : s.value("required", json::array()))
      if (!v.contains(key.get<std::string>()))
        throw std::invalid_argument("Required parameter missing");
    for (const auto &[key, value] : v.items()) {
      if (!properties.contains(key)) {
        if (s.contains("additionalProperties") &&
            s["additionalProperties"].is_object())
          validate_schema(s["additionalProperties"], value, depth + 1);
        else if (!s.value("additionalProperties", true))
          throw std::invalid_argument("Unknown parameter: " + key);
      } else
        validate_schema(properties[key], value, depth + 1);
    }
  }
  if (v.is_array()) {
    if (v.size() > 128)
      throw std::invalid_argument("Parameter array too long");
    for (const auto &value : v)
      if (s.contains("items"))
        validate_schema(s["items"], value, depth + 1);
  }
}
Catalog::Catalog(const std::string &path) {
  auto source = parse_json(read_file(path, 1024 * 1024));
  if (!source.is_array())
    throw std::runtime_error("Invalid task catalog");
  for (auto &entry : source) {
    const auto kind = entry.at("id").get<std::string>();
    if (entries_.contains(kind))
      throw std::runtime_error("Duplicate catalog kind");
    entries_.emplace(kind, std::move(entry));
  }
}
const json &Catalog::get(const std::string &kind) const {
  auto found = entries_.find(kind);
  if (found == entries_.end())
    throw std::invalid_argument("Unsupported task kind");
  return found->second;
}
void Catalog::validate(const std::string &kind, const json &parameters) const {
  const auto &entry = get(kind);
  if (entry.value("agent_runner", true) == false ||
      entry.value("runner", "") == "import")
    throw std::invalid_argument("This kind cannot run on Agent");
  if (parameters.dump().size() > 16384)
    throw std::invalid_argument("Parameters exceed budget");
  validate_schema(entry.at("parameter_schema"), parameters);
  const auto target = parameters.value("target", json::object());
  const auto type = target.value("type", "process");
  const auto supported = entry.at("supported_targets");
  if (std::find(supported.begin(), supported.end(), type) == supported.end())
    throw std::invalid_argument(
        "Target scope is not supported by this task kind");
  if (type == "process" && entry.value("requires_pid", false) &&
      target.value("pid", parameters.value("pid", 0)) <= 1)
    throw std::invalid_argument("A visible process PID is required");
}
} // 结束 dropcore 命名空间
