#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>

namespace dropcore {
class UploadFailure : public std::runtime_error {
public:
  UploadFailure(bool retryable, long status = 0, unsigned retry_after = 0)
      : std::runtime_error(retryable ? "Transient artifact transport failure"
                                     : "Permanent artifact transport failure"),
        retryable(retryable), http_status(status),
        retry_after_seconds(retry_after) {}
  bool retryable;
  long http_status;
  unsigned retry_after_seconds;
};
bool retryable_upload_http(long status);
std::uint64_t upload_retry_delay_ms(unsigned attempt, std::uint32_t entropy);
std::string sha256_file(const std::string &file);
struct UploadReceipt {
  std::string etag;
  std::int64_t completed_unix_ms = 0;
};
UploadReceipt upload_file(const std::string &url, const std::string &file,
                          const std::string &content_type = "text/plain",
                          std::uint64_t max_bytes = 64ULL * 1024 * 1024);
void atomic_write(const std::string &path, const std::string &content);
} // namespace dropcore
