// 本地制品完整性及短期授权上传：校验普通文件、大小与SHA后再向固定目标PUT。
// 对象存储长期凭据留在控制面，Agent只持有本次attempt对应的短期上传授权。
// 原子写与成功标记支撑断点恢复；不跟随重定向，避免把数据发到授权之外的地址。
// 上传完成还需NotifyResult登记，不能把HTTP成功直接当成平台任务成功。

#include "drop/artifact.hpp"

#include <curl/curl.h>
#include <fcntl.h>
#include <openssl/evp.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <memory>
#include <sstream>
#include <stdexcept>

namespace dropcore {
bool retryable_upload_http(long status) {
  return status == 429 || (status >= 500 && status <= 599);
}
std::uint64_t upload_retry_delay_ms(unsigned attempt, std::uint32_t entropy) {
  const auto base =
      std::min<std::uint64_t>(30000, 500ULL << std::min(attempt, 6U));
  return std::min<std::uint64_t>(30000, base / 2 + entropy % base);
}
std::string sha256_file(const std::string &file) {
  std::ifstream input(file, std::ios::binary);
  if (!input)
    throw std::runtime_error("Cannot open artifact for hashing");
  std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> context(
      EVP_MD_CTX_new(), EVP_MD_CTX_free);
  if (!context || EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) != 1)
    throw std::runtime_error("SHA-256 initialization failed");
  std::array<char, 32768> buffer;
  while (input) {
    input.read(buffer.data(), buffer.size());
    if (EVP_DigestUpdate(context.get(), buffer.data(),
                         static_cast<std::size_t>(input.gcount())) != 1)
      throw std::runtime_error("SHA-256 failed");
  }
  if (!input.eof())
    throw std::runtime_error("Artifact read failed");
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest;
  unsigned int length = 0;
  EVP_DigestFinal_ex(context.get(), digest.data(), &length);
  std::ostringstream hex;
  for (unsigned int i = 0; i < length; ++i)
    hex << std::hex << std::setw(2) << std::setfill('0')
        << static_cast<int>(digest[i]);
  return hex.str();
}

namespace {
bool transient_curl(CURLcode code) {
  switch (code) {
  case CURLE_COULDNT_RESOLVE_HOST:
  case CURLE_COULDNT_CONNECT:
  case CURLE_OPERATION_TIMEDOUT:
  case CURLE_SEND_ERROR:
  case CURLE_RECV_ERROR:
  case CURLE_GOT_NOTHING:
  case CURLE_PARTIAL_FILE:
  case CURLE_HTTP2:
  case CURLE_HTTP2_STREAM:
    return true;
  default:
    return false;
  }
}
struct UploadHeaders {
  unsigned retry_after = 0;
  std::string etag;
};
std::size_t upload_header(char *data, std::size_t size, std::size_t count,
                          void *user) {
  const auto bytes = size * count;
  if (bytes > 4096)
    return 0;
  const std::string line(data, bytes);
  auto &headers = *static_cast<UploadHeaders *>(user);
  if (line.rfind("HTTP/", 0) == 0)
    headers = UploadHeaders{};
  auto colon = line.find(':');
  if (colon != std::string::npos) {
    auto name = line.substr(0, colon);
    std::transform(
        name.begin(), name.end(), name.begin(),
        [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    if (name == "etag") {
      const auto start = line.find_first_not_of(" \t", colon + 1);
      const auto end = line.find_last_not_of(" \t\r\n");
      if (start != std::string::npos && end >= start &&
          end - start + 1 <= 1024) {
        const auto value = line.substr(start, end - start + 1);
        if (std::all_of(value.begin(), value.end(), [](unsigned char ch) {
              return ch >= 0x20 && ch < 0x7f;
            }))
          headers.etag = value;
      }
    }
    if (name == "retry-after") {
      const auto start = line.find_first_not_of(" \t", colon + 1);
      if (start != std::string::npos) {
        unsigned seconds = 0;
        const auto result = std::from_chars(line.data() + start,
                                            line.data() + line.size(), seconds);
        if (result.ec == std::errc{})
          headers.retry_after = std::min(seconds, 60U);
      }
    }
  }
  return bytes;
}
std::size_t discard_response(char *, std::size_t size, std::size_t count,
                             void *) {
  return size * count;
}
struct CloseFile {
  void operator()(FILE *file) const {
    if (file)
      std::fclose(file);
  }
};
} // 结束匿名命名空间

UploadReceipt upload_file(const std::string &url, const std::string &file,
                          const std::string &content_type,
                          std::uint64_t max_bytes) {
  if (url.rfind("http://", 0) != 0 && url.rfind("https://", 0) != 0)
    throw std::invalid_argument("Upload URL must use HTTP(S)");
  auto size = std::filesystem::file_size(file);
  if (size == 0 || size > max_bytes || max_bytes > 512ULL * 1024 * 1024)
    throw std::runtime_error("Artifact size outside upload budget");
  struct stat statbuf {};
  if (lstat(file.c_str(), &statbuf) != 0 || !S_ISREG(statbuf.st_mode))
    throw std::runtime_error("Artifact must be a regular file");
  std::unique_ptr<FILE, CloseFile> input(std::fopen(file.c_str(), "rb"));
  if (!input)
    throw std::runtime_error("Cannot open artifact");
  std::unique_ptr<CURL, decltype(&curl_easy_cleanup)> curl(curl_easy_init(),
                                                           curl_easy_cleanup);
  if (!curl)
    throw std::runtime_error("Upload initialization failed");
  curl_slist *raw_headers = nullptr;
  raw_headers =
      curl_slist_append(raw_headers, ("Content-Type: " + content_type).c_str());
  raw_headers = curl_slist_append(raw_headers, "Expect:");
  std::unique_ptr<curl_slist, decltype(&curl_slist_free_all)> headers(
      raw_headers, curl_slist_free_all);
  curl_easy_setopt(curl.get(), CURLOPT_URL, url.c_str());
  curl_easy_setopt(curl.get(), CURLOPT_UPLOAD, 1L);
  curl_easy_setopt(curl.get(), CURLOPT_READDATA, input.get());
  curl_easy_setopt(curl.get(), CURLOPT_INFILESIZE_LARGE,
                   static_cast<curl_off_t>(size));
  curl_easy_setopt(curl.get(), CURLOPT_HTTPHEADER, headers.get());
  curl_easy_setopt(curl.get(), CURLOPT_CONNECTTIMEOUT, 5L);
  curl_easy_setopt(curl.get(), CURLOPT_TIMEOUT, 20L);
  curl_easy_setopt(curl.get(), CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(curl.get(), CURLOPT_PROTOCOLS_STR, "http,https");
  curl_easy_setopt(curl.get(), CURLOPT_WRITEFUNCTION, discard_response);
  UploadHeaders response_headers;
  curl_easy_setopt(curl.get(), CURLOPT_HEADERFUNCTION, upload_header);
  curl_easy_setopt(curl.get(), CURLOPT_HEADERDATA, &response_headers);
  // 上传不跟随重定向：预签名URL只授权一个确定的
  // 目标地址，不能让重定向把制品或授权发送到另一处。
  auto result = curl_easy_perform(curl.get());
  long status = 0;
  curl_easy_getinfo(curl.get(), CURLINFO_RESPONSE_CODE, &status);
  if (result != CURLE_OK)
    throw UploadFailure(transient_curl(result), status,
                        response_headers.retry_after);
  if (status < 200 || status > 299)
    throw UploadFailure(retryable_upload_http(status), status,
                        response_headers.retry_after);
  return {response_headers.etag,
          std::chrono::duration_cast<std::chrono::milliseconds>(
              std::chrono::system_clock::now().time_since_epoch())
              .count()};
}

void atomic_write(const std::string &path, const std::string &content) {
  const std::string temporary = path + ".tmp";
  int fd = open(temporary.c_str(),
                O_WRONLY | O_CREAT | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC, 0600);
  if (fd < 0)
    throw std::runtime_error("Could not open local recovery journal");
  struct stat target {};
  if (fstat(fd, &target) || !S_ISREG(target.st_mode) ||
      target.st_uid != geteuid() || target.st_nlink != 1 || ftruncate(fd, 0)) {
    close(fd);
    throw std::runtime_error(
        "Local recovery journal must be an owned regular file");
  }
  std::size_t written = 0;
  while (written < content.size()) {
    auto count = write(fd, content.data() + written, content.size() - written);
    if (count <= 0) {
      close(fd);
      throw std::runtime_error("Local recovery journal write failed");
    }
    written += static_cast<std::size_t>(count);
  }
  if (fsync(fd) != 0) {
    close(fd);
    throw std::runtime_error("Local recovery journal sync failed");
  }
  close(fd);
  if (rename(temporary.c_str(), path.c_str()) != 0)
    throw std::runtime_error("Local recovery journal commit failed");
  const auto parent = std::filesystem::path(path).parent_path();
  int directory_fd = open(parent.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (directory_fd < 0)
    throw std::runtime_error("Local recovery directory could not be opened");
  const auto synced = fsync(directory_fd);
  close(directory_fd);
  if (synced != 0)
    throw std::runtime_error("Local recovery directory sync failed");
}
} // 结束 dropcore 命名空间
