#pragma once

#include <string>

namespace mini_drop_native {

struct Config {
  std::string grpc_addr;
  std::string agent_id;
  std::string agent_ip;
  std::string grpc_token;
  bool grpc_secure;
  std::string grpc_ca_cert;
  std::string grpc_client_cert;
  std::string grpc_client_key;
  std::string grpc_tls_server_name;
  int heartbeat_sec;
  int max_memory_mb;
  int max_output_mb;
  std::string minio_bucket;
  std::string result_outbox_dir;
  int result_outbox_max_entries;
};

Config load_config();

}  // namespace mini_drop_native
