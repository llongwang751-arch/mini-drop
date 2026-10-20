#pragma once
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <map>
#include <mutex>
#include <nlohmann/json.hpp>
#include <string>
#include <thread>

namespace dropcore {
class Telemetry {
public:
  explicit Telemetry(std::string service, int default_port);
  ~Telemetry();
  void add(const std::string &metric, double value = 1);
  void set(const std::string &metric, double value);
  void duration(const std::string &kind, double seconds, bool success);
  void trace(const std::string &operation, const std::string &request_id,
             std::int64_t task, std::int64_t started_ns, bool success,
             const std::string &traceparent = "");
  std::string render() const;

private:
  std::string service_, endpoint_;
  mutable std::mutex mutex_;
  std::condition_variable condition_;
  std::map<std::string, double> values_;
  std::deque<std::string> traces_;
  std::jthread listener_, exporter_;
  int socket_ = -1;
};
std::int64_t unix_ns();
nlohmann::json certificate_metadata(const std::string &path);
} // namespace dropcore
