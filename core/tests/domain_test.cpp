#include "drop/domain.hpp"

#include <iostream>
#include <stdexcept>

namespace {
void require(bool ok, const char *message) {
  if (!ok)
    throw std::runtime_error(message);
}
template <class F> void rejects(F f) {
  try {
    f();
  } catch (const std::invalid_argument &) {
    return;
  }
  throw std::runtime_error("Invalid input was accepted");
}
} // 结束匿名命名空间

int main() {
  using namespace dropcore;
  validate_priority(-10);
  validate_priority(0);
  validate_priority(10);
  rejects([&] { validate_priority(-11); });
  rejects([&] { validate_priority(11); });
  SamplingParameters p{1234, 5, 99, "process"};
  auto argv = perf_record_argv(p, "/tmp/task/perf.data", 1234);
  require(argv[0] == "/usr/bin/perf", "Executable must use fixed path");
  require(argv[8] == "1234", "PID must be a distinct argv element");
  require(argv.back() == "5", "Duration must be numeric argv element");
  rejects([&] {
    auto bad = p;
    bad.pid = -1;
    validate(bad);
  });
  rejects([&] {
    auto bad = p;
    bad.pid = 1;
    validate(bad);
  });
  rejects([&] {
    auto bad = p;
    bad.duration_seconds = 61;
    validate(bad);
  });
  rejects([&] {
    auto bad = p;
    bad.frequency_hz = 200;
    validate(bad);
  });
  rejects([&] {
    auto bad = p;
    bad.scope = "process; touch /tmp/pwn";
    validate(bad);
  });
  rejects([&] { perf_record_argv(p, "/tmp/task/../perf.data", 1234); });
  rejects([&] { perf_record_argv(p, "/tmp/task/perf.data", 0); });
  require(safe_identifier("agent-prod_1"), "Expected valid identifier");
  require(!safe_identifier("../../etc"), "Must reject path traversal");
  require(!safe_identifier("$(id)"), "Must reject command syntax");
  require(collection_transition_allowed("RUNNING", "UPLOADING"),
          "Valid transition rejected");
  require(!collection_transition_allowed("COLLECTED", "RUNNING"),
          "Terminal state regressed");
  require(!collection_transition_allowed("QUEUED", "COLLECTED"),
          "Skipped transition accepted");
  require(collection_transition_allowed("RUNNING", "CANCELED"),
          "Cancellation rejected");
  require(current_attempt_matches(8, 8),
          "Current attempt cancellation rejected");
  require(!current_attempt_matches(7, 8),
          "Stale cancellation could affect retry");
  require(!current_attempt_matches(0, 8), "Unscoped cancellation accepted");
  std::cout << "domain: 20 checks passed\n";
}
