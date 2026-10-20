// 普通Agent与特权helper之间的本地固定协议，不等同于对外gRPC任务协议。
// 请求token绑定一次工作，collect可只恢复原结果；acknowledge在Agent已持久化后释放helper缓存。
// 可执行路径与上传授权不通过这个接口传入，避免跨权限边界扩大能力。

#pragma once

#include "drop/runner.hpp"
#include <string>

namespace dropcore {
bool helper_kind(const std::string &kind);
bool valid_helper_token(const std::string &token);

struct HelperRequest {
  std::string token;
  RunnerContext context;
  int controlled_pid = 0;
  std::uint64_t binary_max = 0;
};

// 本地helper协议不接收任意路径，独立于gRPC和对象存储上传授权。
nlohmann::json encode_helper_request(const HelperRequest &);
HelperRequest decode_helper_request(const nlohmann::json &, const Catalog &);

class HelperFailure : public std::runtime_error {
public:
  explicit HelperFailure(std::string code);
  std::string error_code;
};

class HelperClient {
public:
  HelperClient(std::string socket_path, const Catalog &catalog);
  nlohmann::json capabilities() const;
  RunnerOutput collect(const HelperRequest &, bool resume_only = false) const;
  void acknowledge(const std::string &token) const;

private:
  std::string socket_path_;
  const Catalog &catalog_;
};

int run_collector_helper();
} // 结束 dropcore 命名空间
