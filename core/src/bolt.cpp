#include "drop/bolt.hpp"
#include <algorithm>
#include <array>
#include <fstream>
#include <sstream>
#include <sys/sysmacros.h>
#include <vector>

namespace dropcore {
namespace {
std::uint64_t little(const unsigned char *value, unsigned bytes) {
  std::uint64_t result = 0;
  for (unsigned index = 0; index < bytes; ++index)
    result |= static_cast<std::uint64_t>(value[index]) << (index * 8);
  return result;
}
bool hexadecimal(const std::string &value) {
  return value.size() == 40 &&
         value.find_first_not_of("0123456789abcdef") == std::string::npos;
}
} // namespace

bool valid_bolt_target_path(const std::string &value) {
  if (value.empty() || value.front() != '/' || value.size() > 4096 ||
      value.ends_with(" (deleted)"))
    return false;
  for (const unsigned char byte : value)
    if (byte < 32 || byte == 127 || byte == ',')
      return false;
  return true;
}

std::string parse_bolt_build_id(const std::string &text) {
  if (text.size() > 80)
    return {};
  const auto first = text.find_first_not_of(" \t\r\n");
  if (first == std::string::npos)
    return {};
  const auto value = text.substr(first, text.find_last_not_of(" \t\r\n") -
                                            first + 1);
  return hexadecimal(value) ? value : std::string{};
}

BoltBuildIdMatch match_bolt_build_ids(const std::string &listing,
                                     const std::string &target_path,
                                     const std::string &expected) {
  if (listing.size() > 16384 || !valid_bolt_target_path(target_path) ||
      !hexadecimal(expected))
    return BoltBuildIdMatch::conflict;
  bool matched = false;
  std::istringstream input(listing);
  std::string line;
  while (std::getline(input, line)) {
    if (line.empty())
      continue;
    const auto split = line.find_first_of(" \t");
    if (split == std::string::npos || !hexadecimal(line.substr(0, split)))
      return BoltBuildIdMatch::conflict;
    const auto start = line.find_first_not_of(" \t", split);
    if (start == std::string::npos)
      return BoltBuildIdMatch::conflict;
    if (line.substr(start) == target_path) {
      if (line.substr(0, split) != expected)
        return BoltBuildIdMatch::conflict;
      matched = true;
    }
  }
  return matched ? BoltBuildIdMatch::matched : BoltBuildIdMatch::missing;
}

bool verify_bolt_perf_mapping(const std::filesystem::path &path,
                              const ProcessIdentity &target,
                              std::uint64_t max_bytes) {
  try {
    if (target.pid <= 1 || !target.executable_inode ||
        !valid_bolt_target_path(target.executable))
      return false;
    const auto size = std::filesystem::file_size(path);
    if (size < 104 || size > max_bytes)
      return false;
    std::ifstream input(path, std::ios::binary);
    std::array<unsigned char, 104> header{};
    if (!input.read(reinterpret_cast<char *>(header.data()), header.size()) ||
        std::string(reinterpret_cast<char *>(header.data()), 8) != "PERFILE2")
      return false;
    const auto header_size = little(header.data() + 8, 8);
    const auto offset = little(header.data() + 40, 8);
    const auto bytes = little(header.data() + 48, 8);
    if (header_size < header.size() || header_size > size ||
        offset < header_size || offset > size || !bytes || bytes > size - offset)
      return false;
    input.seekg(static_cast<std::streamoff>(offset));
    std::uint64_t consumed = 0, records = 0;
    bool found = false;
    while (consumed < bytes) {
      std::array<unsigned char, 8> event{};
      if (++records > 2000000 || bytes - consumed < event.size() ||
          !input.read(reinterpret_cast<char *>(event.data()), event.size()))
        return false;
      const auto event_size = little(event.data() + 6, 2);
      if (event_size < event.size() || event_size % 8 ||
          event_size > bytes - consumed)
        return false;
      std::vector<unsigned char> body(event_size - event.size());
      if (!input.read(reinterpret_cast<char *>(body.data()), body.size()))
        return false;
      consumed += event_size;
      if (little(event.data(), 4) != 10) // PERF_RECORD_MMAP2
        continue;
      if (body.size() < 65)
        return false;
      const auto end = std::find(body.begin() + 64, body.end(), 0);
      if (end == body.end())
        return false;
      const std::string filename(body.begin() + 64, end);
      if (filename != target.executable)
        continue;
      // Require original device/inode MMAP2 records. A pre-supplied build-id
      // mmap record cannot independently prove this frozen executable inode.
      if ((little(event.data() + 4, 2) & (1U << 14)) ||
          little(body.data(), 4) != static_cast<unsigned>(target.pid) ||
          little(body.data() + 32, 4) != major(target.executable_device) ||
          little(body.data() + 36, 4) != minor(target.executable_device) ||
          little(body.data() + 40, 8) != target.executable_inode)
        return false;
      if ((little(body.data() + 56, 4) & 4U) &&
          little(body.data() + 16, 8))
        found = true;
    }
    return found && consumed == bytes;
  } catch (const std::exception &) {
    return false;
  }
}
} // namespace dropcore
