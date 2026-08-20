#include "config.h"

#include <cstdlib>
#include <string>

namespace mini_drop_native {
namespace {

std::string env_or(const char* name, const std::string& fallback) {
  const char* value = std::getenv(name);
  return value && *value ? value : fallback;
}

int env_int(const char* name, int fallback) {
  try {
    return std::stoi(env_or(name, std::to_string(fallback)));
  } catch (...) {
    return fallback;
  }
}

}  // namespace

Config load_config() {
  return Config{
      env_or("AGENT_GRPC_ADDR", "control-plane:50051"),
      env_or("AGENT_ID", "native-agent"),
      env_or("AGENT_IP_ADDR", "127.0.0.1"),
      env_or("MINI_DROP_GRPC_TOKEN", ""),
      env_int("AGENT_HEARTBEAT_INTERVAL_SEC", 5),
      env_int("NATIVE_AGENT_RUNNER_MAX_MEMORY_MB", 1024),
      env_int("NATIVE_AGENT_RUNNER_MAX_OUTPUT_MB", 256),
      env_or("MINIO_ENDPOINT", "minio:9000"),
      env_or("MINIO_ACCESS_KEY", "mini_drop"),
      env_or("MINIO_SECRET_KEY", "mini_drop_secret"),
      env_or("MINIO_BUCKET", "mini-drop"),
      env_or("AGENT_RESULT_OUTBOX_DIR", "/var/lib/mini-drop-agent/outbox"),
      env_int("AGENT_RESULT_OUTBOX_MAX_ENTRIES", 256),
  };
}

}  // namespace mini_drop_native
