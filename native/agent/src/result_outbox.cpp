#include "result_outbox.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <stdexcept>
#include <system_error>
#include <unistd.h>

namespace mini_drop_native {
namespace {

constexpr std::array<char, 8> kMagic{'M', 'D', 'R', 'E', 'S', '0', '1', '\0'};
constexpr std::uint64_t kMaxFieldBytes = 64ULL * 1024ULL * 1024ULL;

void append_u64(std::string& output, std::uint64_t value) {
  for (int shift = 56; shift >= 0; shift -= 8) {
    output.push_back(static_cast<char>((value >> shift) & 0xff));
  }
}

void append_string(std::string& output, const std::string& value) {
  append_u64(output, value.size());
  output.append(value);
}

std::uint64_t read_u64(const std::string& input, std::size_t& offset) {
  if (input.size() - offset < 8) {
    throw std::runtime_error("truncated outbox integer");
  }
  std::uint64_t value = 0;
  for (int index = 0; index < 8; ++index) {
    value = (value << 8) |
        static_cast<unsigned char>(input[offset + static_cast<std::size_t>(index)]);
  }
  offset += 8;
  return value;
}

std::string read_string(const std::string& input, std::size_t& offset) {
  const std::uint64_t size = read_u64(input, offset);
  if (size > kMaxFieldBytes || size > input.size() - offset) {
    throw std::runtime_error("invalid outbox field size");
  }
  std::string value = input.substr(offset, static_cast<std::size_t>(size));
  offset += static_cast<std::size_t>(size);
  return value;
}

std::string serialize(const TaskResult& result) {
  std::string output(kMagic.begin(), kMagic.end());
  output.push_back(result.ok ? '\1' : '\0');
  append_string(output, result.task_id);
  append_string(output, result.error);
  append_string(output, result.artifact_json);
  return output;
}

TaskResult deserialize(const std::string& input) {
  if (input.size() < kMagic.size() + 1 ||
      !std::equal(kMagic.begin(), kMagic.end(), input.begin())) {
    throw std::runtime_error("invalid outbox header");
  }
  std::size_t offset = kMagic.size();
  const unsigned char ok = static_cast<unsigned char>(input[offset++]);
  if (ok > 1) {
    throw std::runtime_error("invalid outbox status");
  }
  TaskResult result;
  result.ok = ok == 1;
  result.task_id = read_string(input, offset);
  result.error = read_string(input, offset);
  result.artifact_json = read_string(input, offset);
  if (result.task_id.empty() || offset != input.size()) {
    throw std::runtime_error("invalid outbox payload");
  }
  return result;
}

std::uint64_t fnv1a(const std::string& value) {
  std::uint64_t hash = 14695981039346656037ULL;
  for (const unsigned char ch : value) {
    hash ^= ch;
    hash *= 1099511628211ULL;
  }
  return hash;
}

std::string hex_hash(std::uint64_t value) {
  constexpr char digits[] = "0123456789abcdef";
  std::string output(16, '0');
  for (int index = 15; index >= 0; --index) {
    output[static_cast<std::size_t>(index)] = digits[value & 0x0f];
    value >>= 4;
  }
  return output;
}

void write_all(int fd, const std::string& data) {
  std::size_t offset = 0;
  while (offset < data.size()) {
    const ssize_t written = ::write(fd, data.data() + offset, data.size() - offset);
    if (written < 0) {
      if (errno == EINTR) continue;
      throw std::system_error(errno, std::generic_category(), "write outbox entry");
    }
    offset += static_cast<std::size_t>(written);
  }
}

void sync_directory(const std::filesystem::path& directory) {
  const int fd = ::open(directory.c_str(), O_RDONLY | O_DIRECTORY);
  if (fd < 0) {
    throw std::system_error(errno, std::generic_category(), "open outbox directory");
  }
  if (::fsync(fd) != 0) {
    const int error = errno;
    ::close(fd);
    throw std::system_error(error, std::generic_category(), "sync outbox directory");
  }
  ::close(fd);
}

std::vector<std::filesystem::path> entry_paths(
    const std::filesystem::path& directory) {
  std::vector<std::filesystem::path> paths;
  for (const auto& item : std::filesystem::directory_iterator(directory)) {
    if (item.is_regular_file() && item.path().extension() == ".outbox") {
      paths.push_back(item.path());
    }
  }
  std::sort(paths.begin(), paths.end(), [](const auto& left, const auto& right) {
    const auto left_time = std::filesystem::last_write_time(left);
    const auto right_time = std::filesystem::last_write_time(right);
    return left_time == right_time ? left.filename() < right.filename()
                                   : left_time < right_time;
  });
  return paths;
}

}

ResultOutbox::ResultOutbox(std::filesystem::path directory,
                           std::size_t max_entries)
    : directory_(std::move(directory)),
      max_entries_(std::max<std::size_t>(1, max_entries)) {
  std::filesystem::create_directories(directory_);
}

OutboxEntry ResultOutbox::enqueue(const TaskResult& result) {
  if (result.task_id.empty()) {
    throw std::invalid_argument("outbox task id must not be empty");
  }
  const auto path = path_for(result.task_id);
  const auto temporary = path.string() + ".tmp";
  const std::string payload = serialize(result);
  const int fd = ::open(temporary.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
  if (fd < 0) {
    throw std::system_error(errno, std::generic_category(), "create outbox entry");
  }
  try {
    write_all(fd, payload);
    if (::fsync(fd) != 0) {
      throw std::system_error(errno, std::generic_category(), "sync outbox entry");
    }
  } catch (...) {
    ::close(fd);
    std::filesystem::remove(temporary);
    throw;
  }
  if (::close(fd) != 0) {
    std::filesystem::remove(temporary);
    throw std::system_error(errno, std::generic_category(), "close outbox entry");
  }
  std::filesystem::rename(temporary, path);
  sync_directory(directory_);
  trim();
  return OutboxEntry{path, result};
}

std::vector<OutboxEntry> ResultOutbox::pending() {
  std::vector<OutboxEntry> entries;
  for (const auto& path : entry_paths(directory_)) {
    try {
      std::ifstream input(path, std::ios::binary);
      if (!input) throw std::runtime_error("open outbox entry");
      const std::string payload{
          std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
      entries.push_back(OutboxEntry{path, deserialize(payload)});
    } catch (...) {
      auto corrupt = path;
      corrupt.replace_extension(".corrupt");
      std::error_code error;
      std::filesystem::rename(path, corrupt, error);
    }
  }
  return entries;
}

void ResultOutbox::acknowledge(const OutboxEntry& entry) const {
  if (std::filesystem::remove(entry.path)) {
    sync_directory(directory_);
  }
}

std::size_t ResultOutbox::replay(
    const std::function<bool(const TaskResult&)>& deliver) {
  std::size_t delivered = 0;
  for (const auto& entry : pending()) {
    if (!deliver(entry.result)) break;
    acknowledge(entry);
    ++delivered;
  }
  return delivered;
}

std::filesystem::path ResultOutbox::path_for(const std::string& task_id) const {
  return directory_ / (hex_hash(fnv1a(task_id)) + ".outbox");
}

void ResultOutbox::trim() {
  const auto paths = entry_paths(directory_);
  if (paths.size() <= max_entries_) return;
  for (std::size_t index = 0; index < paths.size() - max_entries_; ++index) {
    auto overflow = paths[index];
    overflow.replace_extension(".overflow");
    std::filesystem::rename(paths[index], overflow);
  }
  sync_directory(directory_);
}

}
