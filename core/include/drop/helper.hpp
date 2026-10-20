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

// This path-free local contract is independent of gRPC and upload grants.
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
} // namespace dropcore
