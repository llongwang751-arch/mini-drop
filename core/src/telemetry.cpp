// 采集与控制面的可观测性旁路：提供有界指标和追踪导出，帮助关联执行阶段。
// 遥测失败不改变已经提交的业务结果；上下文只携带安全标识，不携带原始profile或凭据。
// 指标用于观察运行状态，不替代数据库里的任务/attempt状态机。

#include "drop/telemetry.hpp"
#include "drop/process.hpp"
#include <arpa/inet.h>
#include <array>
#include <chrono>
#include <ctime>
#include <curl/curl.h>
#include <iomanip>
#include <memory>
#include <openssl/pem.h>
#include <openssl/rand.h>
#include <openssl/x509.h>
#include <regex>
#include <sstream>
#include <sys/socket.h>
#include <unistd.h>

namespace dropcore {
std::int64_t unix_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}
namespace {
std::string random_hex(std::size_t length) {
  std::array<unsigned char, 16> bytes{};
  if (length > bytes.size() ||
      RAND_bytes(bytes.data(), static_cast<int>(length)) != 1)
    throw std::runtime_error("Trace identity generation failed");
  std::ostringstream out;
  out << std::hex << std::setfill('0');
  for (std::size_t i = 0; i < length; ++i)
    out << std::setw(2) << static_cast<unsigned int>(bytes[i]);
  return out.str();
}
std::size_t discard(char *, std::size_t size, std::size_t count, void *) {
  return size * count;
}
} // 结束匿名命名空间
Telemetry::Telemetry(std::string service, int port)
    : service_(std::move(service)) {
  endpoint_ = secret("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT");
  if (endpoint_.empty()) {
    const auto base = secret("OTEL_EXPORTER_OTLP_ENDPOINT");
    if (!base.empty())
      endpoint_ = base + "/v1/traces";
  }
  if (!endpoint_.empty())
    exporter_ = std::jthread([this](std::stop_token stop) {
      while (!stop.stop_requested()) {
        std::string body;
        {
          std::unique_lock lock(mutex_);
          condition_.wait_for(lock, std::chrono::milliseconds(250), [&] {
            return !traces_.empty() || stop.stop_requested();
          });
          if (traces_.empty())
            continue;
          body = std::move(traces_.front());
          traces_.pop_front();
        }
        std::unique_ptr<CURL, decltype(&curl_easy_cleanup)> curl(
            curl_easy_init(), curl_easy_cleanup);
        if (!curl)
          continue;
        auto *headers =
            curl_slist_append(nullptr, "Content-Type: application/json");
        curl_easy_setopt(curl.get(), CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl.get(), CURLOPT_URL, endpoint_.c_str());
        curl_easy_setopt(curl.get(), CURLOPT_POSTFIELDS, body.c_str());
        curl_easy_setopt(curl.get(), CURLOPT_POSTFIELDSIZE,
                         static_cast<long>(body.size()));
        curl_easy_setopt(curl.get(), CURLOPT_TIMEOUT_MS, 1500L);
        curl_easy_setopt(curl.get(), CURLOPT_CONNECTTIMEOUT_MS, 500L);
        curl_easy_setopt(curl.get(), CURLOPT_NOSIGNAL, 1L);
        curl_easy_setopt(curl.get(), CURLOPT_PROXY, "");
        curl_easy_setopt(curl.get(), CURLOPT_PROTOCOLS_STR, "http,https");
        curl_easy_setopt(curl.get(), CURLOPT_WRITEFUNCTION, discard);
        auto result = curl_easy_perform(curl.get());
        long status = 0;
        curl_easy_getinfo(curl.get(), CURLINFO_RESPONSE_CODE, &status);
        curl_slist_free_all(headers);
        if (result != CURLE_OK || status / 100 != 2)
          add("drop_otel_export_errors_total");
      }
    });
  const auto address =
      secret("METRICS_LISTEN", "127.0.0.1:" + std::to_string(port));
  if (address == "off")
    return;
  const auto colon = address.rfind(':');
  if (colon == std::string::npos)
    throw std::invalid_argument("METRICS_LISTEN requires IPv4:port");
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  const auto number = std::stoi(address.substr(colon + 1));
  if (number < 1 || number > 65535 ||
      inet_pton(AF_INET, address.substr(0, colon).c_str(), &addr.sin_addr) != 1)
    throw std::invalid_argument("Invalid metrics address");
  addr.sin_port = htons(static_cast<uint16_t>(number));
  socket_ = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (socket_ < 0)
    throw std::runtime_error("Metrics socket unavailable");
  int reuse = 1;
  setsockopt(socket_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
  if (bind(socket_, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) != 0 ||
      listen(socket_, 8) != 0) {
    close(socket_);
    socket_ = -1;
    throw std::runtime_error("Metrics listener bind failed");
  }
  listener_ = std::jthread([this](std::stop_token stop) {
    while (!stop.stop_requested()) {
      const auto client = accept4(socket_, nullptr, nullptr, SOCK_CLOEXEC);
      if (client < 0)
        continue;
      timeval timeout{1, 0};
      setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
      setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
      std::array<char, 2048> request{};
      const auto count = recv(client, request.data(), request.size(), 0);
      const bool metrics =
          count > 0 &&
          std::string_view(request.data(), static_cast<std::size_t>(count))
              .starts_with("GET /metrics ");
      const auto body = metrics ? render() : "not found\n";
      const auto response =
          std::string("HTTP/1.1 ") + (metrics ? "200 OK" : "404 Not Found") +
          "\r\nContent-Type: text/plain; version=0.0.4\r\nConnection: "
          "close\r\nContent-Length: " +
          std::to_string(body.size()) + "\r\n\r\n" + body;
      std::size_t sent = 0;
      while (sent < response.size()) {
        auto n = send(client, response.data() + sent, response.size() - sent,
                      MSG_NOSIGNAL);
        if (n <= 0)
          break;
        sent += static_cast<std::size_t>(n);
      }
      close(client);
    }
  });
}
Telemetry::~Telemetry() {
  listener_.request_stop();
  exporter_.request_stop();
  condition_.notify_all();
  if (socket_ >= 0) {
    shutdown(socket_, SHUT_RDWR);
    close(socket_);
  }
}
void Telemetry::add(const std::string &key, double value) {
  std::lock_guard lock(mutex_);
  values_[key] += value;
}
void Telemetry::set(const std::string &key, double value) {
  std::lock_guard lock(mutex_);
  values_[key] = value;
}
void Telemetry::duration(const std::string &kind, double seconds,
                         bool success) {
  const auto labels = "{kind=\"" + kind + "\"}";
  add("drop_runner_duration_seconds_sum" + labels, seconds);
  add("drop_runner_duration_seconds_count" + labels);
  if (!success)
    add("drop_runner_errors_total" + labels);
}
std::string Telemetry::render() const {
  std::lock_guard lock(mutex_);
  std::ostringstream out;
  out << "# drop core runtime metrics\n";
  for (const auto &[key, value] : values_)
    out << key << ' ' << std::setprecision(12) << value << '\n';
  return out.str();
}
void Telemetry::trace(const std::string &operation,
                      const std::string &request_id, std::int64_t task,
                      std::int64_t started, bool success,
                      const std::string &parent) {
  if (endpoint_.empty())
    return;
  using json = nlohmann::json;
  json span = {{"traceId", random_hex(16)},
               {"spanId", random_hex(8)},
               {"name", operation},
               {"kind", 1},
               {"startTimeUnixNano", std::to_string(started)},
               {"endTimeUnixNano", std::to_string(unix_ns())},
               {"status", {{"code", success ? 1 : 2}}}};
  span["attributes"] =
      json::array({{{"key", "drop.task_id"},
                    {"value", {{"intValue", std::to_string(task)}}}},
                   {{"key", "drop.request_id"},
                    {"value", {{"stringValue", request_id.substr(0, 128)}}}}});
  if (std::regex_match(
          parent, std::regex("00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}")) &&
      parent.substr(3, 32) != std::string(32, '0') &&
      parent.substr(36, 16) != std::string(16, '0'))
    span["links"] = json::array({{{"traceId", parent.substr(3, 32)},
                                  {"spanId", parent.substr(36, 16)}}});
  const json scope = {{"scope", {{"name", "drop.core"}, {"version", "2"}}},
                      {"spans", json::array({span})}};
  const json resource = {
      {"resource",
       {{"attributes",
         json::array({{{"key", "service.name"},
                       {"value", {{"stringValue", service_}}}}})}}},
      {"scopeSpans", json::array({scope})}};
  const auto body = json{{"resourceSpans", json::array({resource})}}.dump();
  std::lock_guard lock(mutex_);
  if (traces_.size() < 128) {
    traces_.push_back(body);
    condition_.notify_one();
  } else
    values_["drop_otel_dropped_spans_total"] += 1;
}
nlohmann::json certificate_metadata(const std::string &path) {
  std::unique_ptr<BIO, decltype(&BIO_free)> bio(BIO_new_file(path.c_str(), "r"),
                                                BIO_free);
  if (!bio)
    throw std::runtime_error("Certificate cannot be read");
  std::unique_ptr<X509, decltype(&X509_free)> cert(
      PEM_read_bio_X509(bio.get(), nullptr, nullptr, nullptr), X509_free);
  if (!cert)
    throw std::runtime_error("Certificate cannot be decoded");
  int days = 0, seconds = 0;
  if (ASN1_TIME_diff(&days, &seconds, nullptr,
                     X509_get0_notAfter(cert.get())) != 1)
    throw std::runtime_error("Certificate expiry cannot be decoded");
  std::array<char, 256> name{};
  X509_NAME_get_text_by_NID(X509_get_subject_name(cert.get()), NID_commonName,
                            name.data(), static_cast<int>(name.size()));
  std::tm expires{};
  if (ASN1_TIME_to_tm(X509_get0_notAfter(cert.get()), &expires) != 1)
    throw std::runtime_error("Certificate absolute expiry cannot be decoded");
  return {
      {"common_name", name.data()},
      {"not_after_unix_seconds", static_cast<std::int64_t>(timegm(&expires))},
      {"expires_in_seconds", static_cast<std::int64_t>(days) * 86400 + seconds},
      {"expired", days < 0 || (days == 0 && seconds < 0)}};
}
} // 结束 dropcore 命名空间
