// 采集清单接口：把允许公开的执行条件与制品校验信息组合为可追溯证据。
// 此处只负责采集清单，不推断性能根因，也不代替分析器的结果完整性检查。

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
// 清单字段采用显式允许列表，排除业务路径、凭据及未经处理的原始元数据。
std::string build_collection_manifest(const CollectionManifest &);
} // 结束 dropcore 命名空间
