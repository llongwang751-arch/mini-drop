#include "drop/artifact.hpp"

#include <array>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

namespace {
void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}

void http_classification() {
  for (const long status : {429L, 500L, 502L, 503L, 504L, 599L})
    require(dropcore::retryable_upload_http(status),
            "Transient upload HTTP failure was not retryable");
  // 此处验证明确的策略边界，不属于导入或实测的网络传输数据。
  for (const long status : {0L,
                            100L,
                            200L,
                            204L,
                            299L,
                            301L,
                            307L,
                            400L,
                            401L,
                            403L,
                            404L,
                            408L,
                            409L,
                            413L,
                            422L,
                            428L,
                            430L,
                            499L,
                            600L,
                            1000L,
                            -1L,
                            std::numeric_limits<long>::min(),
                            std::numeric_limits<long>::max()})
    require(!dropcore::retryable_upload_http(status),
            "Permanent/non-error HTTP response caused an upload retry");
}

void bounded_exponential_jitter() {
  using dropcore::upload_retry_delay_ms;
  require(upload_retry_delay_ms(0, 0) == 250,
          "First upload backoff lower boundary changed");
  require(upload_retry_delay_ms(0, 499) == 749,
          "First upload backoff upper jitter boundary changed");
  require(upload_retry_delay_ms(0, 500) == 250,
          "Upload jitter did not remain bounded at modulo boundary");
  require(upload_retry_delay_ms(1, 0) == 500 &&
              upload_retry_delay_ms(2, 0) == 1000 &&
              upload_retry_delay_ms(5, 0) == 8000,
          "Upload retries did not grow exponentially");
  require(upload_retry_delay_ms(6, 0) == 15000 &&
              upload_retry_delay_ms(6, 29999) == 30000,
          "Upload delay cap or capped jitter boundary changed");
  const std::array<std::uint32_t, 8> entropy{
      0,     1,     499,   500,
      15999, 29999, 30000, std::numeric_limits<std::uint32_t>::max()};
  for (const auto value : entropy) {
    require(upload_retry_delay_ms(0, value) >= 250 &&
                upload_retry_delay_ms(0, value) <= 749,
            "Initial upload jitter escaped its time budget");
    require(upload_retry_delay_ms(5, value) >= 8000 &&
                upload_retry_delay_ms(5, value) <= 23999,
            "Exponential upload jitter escaped its time budget");
    require(upload_retry_delay_ms(6, value) >= 15000 &&
                upload_retry_delay_ms(6, value) <= 30000,
            "Capped upload jitter escaped its time budget");
    require(upload_retry_delay_ms(std::numeric_limits<unsigned>::max(),
                                  value) == upload_retry_delay_ms(6, value),
            "Extreme upload attempt overflowed the capped backoff");
  }
  require(upload_retry_delay_ms(2, 1) != upload_retry_delay_ms(2, 1000),
          "Upload retry entropy did not spread reconnect attempts");
}

void safe_typed_failure() {
  const dropcore::UploadFailure temporary(true, 503, 17);
  require(temporary.retryable && temporary.http_status == 503 &&
              temporary.retry_after_seconds == 17,
          "Typed transient upload metadata was lost");
  require(std::string(temporary.what()) ==
              "Transient artifact transport failure",
          "Transient upload exception exposed unapproved diagnostic text");
  const dropcore::UploadFailure permanent(false,
                                          std::numeric_limits<long>::max(),
                                          std::numeric_limits<unsigned>::max());
  require(!permanent.retryable &&
              permanent.http_status == std::numeric_limits<long>::max() &&
              permanent.retry_after_seconds ==
                  std::numeric_limits<unsigned>::max(),
          "Typed permanent upload metadata was truncated");
  require(std::string(permanent.what()) ==
              "Permanent artifact transport failure",
          "Permanent upload exception exposed URL/path/credential details");
  const dropcore::UploadFailure network(true);
  require(network.retryable && network.http_status == 0 &&
              network.retry_after_seconds == 0,
          "Transport-only upload failure defaults changed");
  try {
    throw temporary;
  } catch (const std::runtime_error &error) {
    const auto *typed = dynamic_cast<const dropcore::UploadFailure *>(&error);
    require(typed != nullptr && typed->retryable && typed->http_status == 503,
            "Upload failure lost its type through the exception base class");
  }
}
} // 结束匿名命名空间

int main() {
  try {
    http_classification();
    bounded_exponential_jitter();
    safe_typed_failure();
    std::cout << "upload policy, jitter, and safe failure tests passed\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
