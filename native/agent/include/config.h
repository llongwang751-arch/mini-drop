#pragma once

#include <string>

namespace mini_drop_native {

struct Config {
  std::string grpc_addr;
  std::string agent_id;
  std::string agent_ip;
  std::string grpc_token;
  int heartbeat_sec;
  int max_memory_mb;
  int max_output_mb;
  std::string minio_endpoint;
  std::string minio_access;
  std::string minio_secret;
  std::string minio_bucket;
  std::string result_outbox_dir;
  int result_outbox_max_entries;
};

Config load_config();

}  // namespace mini_drop_native
