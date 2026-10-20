// 本地配置与安全元数据读取：包括证书、容器环境和工具完整性相关信息。
// 配置只从受信环境或受限文件读取，错误路径不回显未经校验的输入或敏感内容。
// 输出的配置摘要用于解释节点运行条件，不作为绕过目标授权的请求参数。

#include "drop/config.hpp"
#include "drop/json.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <fcntl.h>
#include <iomanip>
#include <memory>
#include <openssl/evp.h>
#include <set>
#include <sstream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

namespace dropcore {
namespace {
using json = nlohmann::json;
constexpr std::size_t file_limit = 65536, value_limit = 4096;
const std::set<std::string> known{"AGENT_CONFIG_VERSION",
                                  "AGENT_HOSTNAME",
                                  "AGENT_ID",
                                  "AGENT_IP",
                                  "ALLOWED_CONTAINER_IDS",
                                  "ALLOWED_TARGET_UIDS",
                                  "ALLOW_CONTAINERS",
                                  "ALLOW_HOST_PROCESSES",
                                  "ALLOW_HOST_RESOURCES",
                                  "ALLOW_KERNEL_LOGS",
                                  "ASPROF_EXECUTABLE",
                                  "ASPROF_SHA256",
                                  "BPFTRACE_EXECUTABLE",
                                  "BPFTRACE_SHA256",
                                  "BUILD_COMMIT",
                                  "COLLECTOR_CPU_AFFINITY",
                                  "COLLECTOR_HELPER_AGENT_GID",
                                  "COLLECTOR_HELPER_AGENT_UID",
                                  "COLLECTOR_HELPER_DISK_BYTES",
                                  "COLLECTOR_HELPER_INCOMING_DIR",
                                  "COLLECTOR_HELPER_REQUIRE_BOUNDED_FS",
                                  "COLLECTOR_HELPER_RETENTION_SECONDS",
                                  "COLLECTOR_HELPER_SOCKET",
                                  "COLLECTOR_HELPER_WORK_DIR",
                                  "CTR_EXECUTABLE",
                                  "CTR_SHA256",
                                  "DATABASE_URL",
                                  "DOCKER_EXECUTABLE",
                                  "DOCKER_SHA256",
                                  "DROP_SERVER_ADDRESS",
                                  "DROP_SERVER_ADDRESSES",
                                  "GDB_EXECUTABLE",
                                  "GDB_SHA256",
                                  "GRPC_LISTEN",
                                  "JCMD_EXECUTABLE",
                                  "JCMD_SHA256",
                                  "LLDB_EXECUTABLE",
                                  "LLDB_SHA256",
                                  "MEMRAY_EXECUTABLE",
                                  "MEMRAY_SHA256",
                                  "METRICS_LISTEN",
                                  "NATIVE_CONTROL_ROOT",
                                  "OTEL_EXPORTER_OTLP_ENDPOINT",
                                  "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                                  "PERF2BOLT_EXECUTABLE",
                                  "PERF2BOLT_SHA256",
                                  "PERF_EXECUTABLE",
                                  "PERF_SHA256",
                                  "PY_SPY_EXECUTABLE",
                                  "PY_SPY_SHA256",
                                  "RAW_RECOVERY_DISK_BYTES",
                                  "RAW_RECOVERY_RETENTION_SECONDS",
                                  "RUN_CONTROLLED_WORKLOAD",
                                  "TASK_KINDS_FILE",
                                  "TLS_CA_FILE",
                                  "TLS_CERT_FILE",
                                  "TLS_KEY_FILE",
                                  "TOOL_MANIFEST_FILE",
                                  "WORK_DIR"};
const std::set<std::string> boolean_keys{"ALLOW_CONTAINERS",
                                         "ALLOW_HOST_PROCESSES",
                                         "ALLOW_HOST_RESOURCES",
                                         "ALLOW_KERNEL_LOGS",
                                         "COLLECTOR_HELPER_REQUIRE_BOUNDED_FS",
                                         "RUN_CONTROLLED_WORKLOAD"};
void typed_value(const std::string &key, const std::string &value) {
  if (boolean_keys.contains(key) && value != "true" && value != "false")
    throw std::invalid_argument(
        "Boolean configuration must be exactly true or false");
  const auto uid = [](const std::string &part, bool nonzero) {
    if (part.empty() ||
        part.find_first_not_of("0123456789") != std::string::npos ||
        part.size() > 10 || std::stoull(part) > 4294967295ULL ||
        (nonzero && std::stoull(part) == 0))
      throw std::invalid_argument(
          "Identity configuration exceeds its unsigned32-bit domain");
  };
  if (key == "COLLECTOR_HELPER_AGENT_UID" ||
      key == "COLLECTOR_HELPER_AGENT_GID")
    uid(value, true);
  if (key == "ALLOWED_TARGET_UIDS") {
    std::size_t start = 0;
    for (;;) {
      const auto separator = value.find(',', start);
      uid(value.substr(start, separator == std::string::npos
                                  ? separator
                                  : separator - start),
          false);
      if (separator == std::string::npos)
        break;
      start = separator + 1;
    }
  }
}
void text(const std::string &value, std::size_t maximum = value_limit) {
  if (value.size() > maximum)
    throw std::invalid_argument("Configuration value exceeds its bound");
  (void)json(value).dump(); // 非法UTF-8直接拒绝，不先渲染可疑字节，避免错误路径泄漏输入。
  for (std::size_t index = 0; index < value.size();) {
    const auto first = static_cast<unsigned char>(value[index++]);
    std::uint32_t point = first;
    unsigned continuation = 0;
    if ((first & 0xe0) == 0xc0) {
      point = first & 31;
      continuation = 1;
    } else if ((first & 0xf0) == 0xe0) {
      point = first & 15;
      continuation = 2;
    } else if ((first & 0xf8) == 0xf0) {
      point = first & 7;
      continuation = 3;
    }
    while (continuation--)
      point = (point << 6) | (static_cast<unsigned char>(value[index++]) & 63);
    if (point < 32 || (point >= 127 && point <= 159) ||
        (point >= 0x200b && point <= 0x200f) ||
        (point >= 0x2028 && point <= 0x202e) ||
        (point >= 0x2066 && point <= 0x2069))
      throw std::invalid_argument("Configuration contains control characters");
  }
}
bool registered(const std::string &key) {
  return known.contains(key) || (key.ends_with("_FILE") &&
                                 known.contains(key.substr(0, key.size() - 5)));
}
void explicit_value(const std::string &key, const std::string &value) {
  if (!registered(key))
    throw std::invalid_argument("Unknown configuration key");
  text(value);
  if (!key.ends_with("_FILE"))
    typed_value(key, value);
  if (sensitive_configuration_key(key) && !key.ends_with("_FILE"))
    throw std::invalid_argument("Sensitive configuration requires an "
                                "environment variable or file reference");
  if (key.ends_with("_FILE") && (value.empty() || value.front() != '/'))
    throw std::invalid_argument(
        "Configuration file references must be absolute");
}
std::string bounded_file(const std::string &path) {
  text(path);
  if (path.empty() || path.front() != '/')
    throw std::invalid_argument("Configuration file path must be absolute");
  const auto descriptor =
      open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
  if (descriptor < 0)
    throw std::runtime_error("Configuration file could not be opened");
  struct Close {
    int fd;
    ~Close() { close(fd); }
  } closer{descriptor};
  struct stat before {
  }, after{};
  if (fstat(descriptor, &before) || !S_ISREG(before.st_mode) ||
      before.st_nlink != 1 ||
      (before.st_uid != 0 && before.st_uid != geteuid()) ||
      (before.st_mode & 0022) || before.st_size < 0 ||
      static_cast<std::uint64_t>(before.st_size) > file_limit)
    throw std::runtime_error(
        "Configuration file must be a bounded, protected regular file");
  std::string result;
  std::array<char, 4096> block{};
  for (;;) {
    const auto size = read(descriptor, block.data(), block.size());
    if (size < 0 && errno == EINTR)
      continue;
    if (size < 0 ||
        (size > 0 &&
         result.size() + static_cast<std::size_t>(size) > file_limit))
      throw std::runtime_error("Configuration file read exceeded its bound");
    if (!size)
      break;
    result.append(block.data(), static_cast<std::size_t>(size));
  }
  if (fstat(descriptor, &after) || before.st_size != after.st_size ||
      before.st_mtim.tv_sec != after.st_mtim.tv_sec ||
      before.st_mtim.tv_nsec != after.st_mtim.tv_nsec ||
      before.st_ctim.tv_sec != after.st_ctim.tv_sec ||
      before.st_ctim.tv_nsec != after.st_ctim.tv_nsec)
    throw std::runtime_error("Configuration changed while being read");
  return result;
}
std::string digest(const std::string &value) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> bytes{};
  unsigned size{};
  if (EVP_Digest(value.data(), value.size(), bytes.data(), &size, EVP_sha256(),
                 nullptr) != 1)
    throw std::runtime_error("Configuration digest failed");
  std::ostringstream result;
  for (unsigned index = 0; index < size; ++index)
    result << std::hex << std::setw(2) << std::setfill('0')
           << static_cast<unsigned>(bytes[index]);
  return result.str();
}
std::string public_endpoint(const std::string &value) {
  const auto separator = value.find("://");
  if (separator == std::string::npos)
    return value;
  const auto scheme = value.substr(0, separator);
  if (scheme != "http" && scheme != "https")
    return "[configured]";
  const auto end = value.find_first_of("/?#", separator + 3);
  auto authority = value.substr(
      separator + 3, end == std::string::npos ? end : end - separator - 3);
  if (const auto user = authority.rfind('@'); user != std::string::npos)
    authority.erase(0, user + 1);
  if (authority.empty() || authority.size() > 255 ||
      authority.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQR"
                                  "STUVWXYZ0123456789._:-[]") !=
          std::string::npos)
    return "[configured]";
  return scheme + "://" + authority + "/[redacted]";
}
std::mutex global_mutex;
std::shared_ptr<Configuration> current;
std::shared_ptr<Configuration> active_configuration() {
  std::lock_guard lock(global_mutex);
  if (!current)
    current = std::make_shared<Configuration>();
  return current;
}
} // 结束匿名命名空间
bool sensitive_configuration_key(const std::string &name) {
  return name == "DATABASE_URL" || name.find("PASSWORD") != std::string::npos ||
         name.find("SECRET") != std::string::npos ||
         name.find("TOKEN") != std::string::npos ||
         name.find("PRIVATE_KEY") != std::string::npos ||
         name.starts_with("TLS_KEY");
}
Configuration::Configuration(const std::vector<std::string> &arguments,
                             EnvironmentLookup environment)
    : environment_(std::move(environment)) {
  if (arguments.size() > 257)
    throw std::invalid_argument("Too many configuration arguments");
  std::string file;
  for (std::size_t index = 0; index < arguments.size(); ++index) {
    const auto &argument = arguments[index];
    if ((argument != "--config" && argument != "--set") ||
        index + 1 >= arguments.size())
      throw std::invalid_argument(
          "Use --config absolute.json or --set KEY=VALUE");
    const auto &value = arguments[++index];
    text(value, value_limit + 128);
    if (argument == "--config") {
      if (!file.empty())
        throw std::invalid_argument("Only one configuration file is allowed");
      file = value;
    } else {
      const auto separator = value.find('=');
      if (separator == std::string::npos)
        throw std::invalid_argument(
            "Configuration override requires KEY=VALUE");
      const auto key = value.substr(0, separator),
                 selected = value.substr(separator + 1);
      explicit_value(key, selected);
      if (!cli_.emplace(key, selected).second)
        throw std::invalid_argument("Duplicate configuration override");
    }
  }
  if (!file.empty()) {
    const auto parsed = parse_json(bounded_file(file), file_limit);
    if (!parsed.is_object() || parsed.size() > 128)
      throw std::invalid_argument(
          "Configuration must be a bounded flat object");
    for (const auto &entry : parsed.items()) {
      if (!entry.value().is_string() && !entry.value().is_boolean() &&
          !entry.value().is_number_integer())
        throw std::invalid_argument(
            "Configuration values must be strings, booleans or integers");
      const auto value = entry.value().is_string()
                             ? entry.value().get<std::string>()
                             : entry.value().dump();
      explicit_value(entry.key(), value);
      file_.emplace(entry.key(), value);
    }
  }
}
std::optional<std::string>
Configuration::environment(const std::string &name) const {
  if (environment_)
    return environment_(name);
  if (const auto value = std::getenv(name.c_str()))
    return std::string(value);
  return std::nullopt;
}
std::string Configuration::get(const std::string &name,
                               const std::string &fallback) const {
  const auto file_key = name + "_FILE";
  std::optional<std::string> selected;
  std::string source = "default";
  bool reference = false;
  for (const auto &tier :
       {std::string("cli"), std::string("environment"), std::string("file")}) {
    const auto lookup =
        [&](const std::string &key) -> std::optional<std::string> {
      if (tier == "environment")
        return environment(key);
      const auto &values = tier == "cli" ? cli_ : file_;
      if (const auto found = values.find(key); found != values.end())
        return found->second;
      return std::nullopt;
    };
    if (auto candidate = lookup(file_key)) {
      selected = candidate;
      reference = true;
    } else
      selected = lookup(name);
    if (selected) {
      source = tier + (reference ? "-file" : "");
      break;
    }
  }
  std::string value = selected.value_or(fallback);
  if (reference) {
    value = bounded_file(value);
    while (!value.empty() && (value.back() == '\n' || value.back() == '\r'))
      value.pop_back();
  }
  text(value);
  typed_value(name, value);
  const auto secret = sensitive_configuration_key(name);
  {
    std::lock_guard lock(mutex_);
    values_[name] = secret ? json(value.empty() ? "[unset]" : "[configured]")
                           : json(public_endpoint(value));
    sources_[name] = source;
  }
  return value;
}
json Configuration::snapshot() const {
  // 显式配置过的键都参与配置记录，即使本次二进制尚未读取
  // 它们的值；未启用且未配置的默认项不凭空补造。
  for (const auto &name : known) {
    const auto present = [&](const auto &values) {
      return values.contains(name) || values.contains(name + "_FILE");
    };
    if (present(cli_) || present(file_) || environment(name) ||
        environment(name + "_FILE"))
      (void)get(name);
  }
  std::lock_guard lock(mutex_);
  return {{"values", values_},
          {"sources", sources_},
          {"sha256", digest(values_.dump())},
          {"hash_scope", "canonical resolved values after redaction; secret "
                         "plaintext and secret-file content are excluded"}};
}
void initialize_configuration(int argc, char **argv) {
  std::vector<std::string> arguments;
  for (int index = 1; index < argc; ++index)
    arguments.emplace_back(argv[index]);
  auto next = std::make_shared<Configuration>(arguments);
  std::lock_guard lock(global_mutex);
  current = std::move(next);
}
std::string configuration_value(const std::string &name,
                                const std::string &fallback) {
  return active_configuration()->get(name, fallback);
}
json configuration_snapshot() { return active_configuration()->snapshot(); }
} // 结束 dropcore 命名空间
