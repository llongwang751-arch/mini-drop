// 生成采集端制品清单，用固定允许字段记录版本、质量、文件大小及SHA等可追溯信息。
// 该清单属于采集阶段，不是Analyzer生成的结果清单；两者职责与对象键独立。
// 清单不包含预签名URL、凭据、业务路径或完整stderr，避免“增加证据”反而泄漏原始现场。
// 恢复时重传同一份已冻结字节，让哈希校验与幂等登记保持一致。

#include "drop/manifest.hpp"
#include <algorithm>
#include <set>
#include <stdexcept>

namespace dropcore {
namespace {
bool token(const std::string &value, std::size_t maximum,
           const std::string &alphabet) {
  return !value.empty() && value.size() <= maximum &&
         value.find_first_not_of(alphabet) == std::string::npos;
}
std::string version(const std::string &input) {
  auto value = input.substr(0, input.find_first_of("\r\n")).substr(0, 256);
  if (value.find("://") != std::string::npos ||
      std::any_of(value.begin(), value.end(),
                  [](unsigned char ch) { return ch < 0x20 || ch >= 0x7f; }))
    return "unavailable";
  return value;
}
} // 结束匿名命名空间
std::string build_collection_manifest(const CollectionManifest &context) {
  using json = nlohmann::json;
  const std::string alphabet =
      "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-";
  if (context.task_id <= 0 || context.attempt_id <= 0 ||
      !token(context.kind, 64, alphabet) ||
      !token(context.agent_id, 128, alphabet) || context.started_ms <= 0 ||
      context.finished_ms < context.started_ms ||
      context.uploaded_ms < context.finished_ms)
    throw std::invalid_argument(
        "Invalid collection manifest identity or timing");
  json parameters = json::object(), tools = json::array(),
       files = json::array();
  for (const auto *key :
       {"pid", "duration_seconds", "frequency_hz", "scope", "target_type",
        "event", "call_graph", "category", "template_id", "sample_type", "mode",
        "profile_type", "native", "live", "min_duration_us", "max_events",
        "stack_depth", "port"}) {
    if (!context.parameters.contains(key))
      continue;
    const auto &value = context.parameters.at(key);
    if (value.is_boolean() || value.is_number_integer())
      parameters[key] = value;
    else if (value.is_string() &&
             token(value.get<std::string>(), 64, alphabet + ":"))
      parameters[key] = value;
  }
  if (!context.tools.is_array() || context.tools.size() > 16 ||
      !context.files.is_array() || context.files.empty() ||
      context.files.size() > 4)
    throw std::invalid_argument(
        "Collection manifest exceeds structural budget");
  for (const auto &tool : context.tools) {
    const auto name = tool.value("name", "");
    if (!token(name, 64, alphabet))
      throw std::invalid_argument("Unsafe tool name");
    tools.push_back({{"name", name},
                     {"version", version(tool.value("version", "unavailable"))},
                     {"sha256", tool.value("sha256", "")}});
  }
  unsigned raw_count = 0;
  std::set<std::string> names;
  for (const auto &file : context.files) {
    const auto name = file.value("name", "");
    const auto key = file.value("object_key", "");
    const auto kind = file.value("kind", "");
    const auto hash = file.value("sha256", "");
    const auto size = file.value("size", std::int64_t{0});
    if (!token(name, 128, alphabet) || !token(key, 1024, alphabet + "/") ||
        key.find("/../") != std::string::npos || key.rfind("../", 0) == 0 ||
        !names.insert(name).second || (kind != "RAW" && kind != "BINARY") ||
        hash.size() != 64 ||
        hash.find_first_not_of("0123456789abcdef") != std::string::npos ||
        size <= 0 || size > 512LL * 1024 * 1024)
      throw std::invalid_argument("Invalid data file in collection manifest");
    raw_count += kind == "RAW";
    json entry = {{"kind", kind}, {"name", name},   {"object_key", key},
                  {"size", size}, {"sha256", hash}, {"etag", nullptr}};
    if (file.contains("etag") && file["etag"].is_string()) {
      const auto etag = file["etag"].get<std::string>();
      if (etag.size() > 1024 || etag.find("://") != std::string::npos ||
          std::any_of(etag.begin(), etag.end(),
                      [](unsigned char ch) { return ch < 0x20 || ch >= 0x7f; }))
        throw std::invalid_argument("Invalid opaque PUT ETag receipt");
      if (!etag.empty())
        entry["etag"] = etag;
    }
    files.push_back(entry);
  }
  if (raw_count != 1)
    throw std::invalid_argument("Collection manifest requires exactly one RAW");
  const json manifest = {
      {"schema_version", 1},
      {"format", "collection_manifest_v1"},
      {"task_id", context.task_id},
      {"attempt_id", context.attempt_id},
      {"task_kind", context.kind},
      {"agent",
       {{"id", context.agent_id},
        {"version", version(context.agent_version)},
        {"build_commit", version(context.build_commit)}}},
      {"runner", {{"kind", context.kind}, {"version", "2.0.0"}}},
      {"tools", tools},
      {"collection_started_unix_ms", context.started_ms},
      {"collection_finished_unix_ms", context.finished_ms},
      {"data_upload_completed_unix_ms", context.uploaded_ms},
      {"parameters", parameters},
      {"files", files},
      {"integrity", "SHA256 is independent of opaque HTTP ETag; manifest does "
                    "not recursively hash itself"}};
  const auto serialized = manifest.dump() + "\n";
  if (serialized.size() > 65536)
    throw std::invalid_argument("Collection manifest byte budget exceeded");
  return serialized;
}
} // 结束 dropcore 命名空间
