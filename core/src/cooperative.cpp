#include "drop/cooperative.hpp"
#include "drop/artifact.hpp"
#include "drop/domain.hpp"
#include "drop/json.hpp"
#include "drop/process.hpp"
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <openssl/rand.h>
#include <set>
#include <sys/stat.h>
#include <unistd.h>

namespace fs = std::filesystem;
using json = nlohmann::json;
namespace dropcore {
namespace {
struct Descriptor {
  int value;
  ~Descriptor() {
    if (value >= 0)
      close(value);
  }
};
void directory_sync(const fs::path &path) {
  Descriptor descriptor{
      open(path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW)};
  if (descriptor.value < 0 || fsync(descriptor.value))
    throw std::runtime_error(
        "Cooperative receive directory could not be synced");
}
json marker(const fs::path &journal) {
  Descriptor input{open((journal / "cooperative-output.json").c_str(),
                        O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW)};
  struct stat status {};
  if (input.value < 0 || fstat(input.value, &status) ||
      !S_ISREG(status.st_mode) || status.st_uid != geteuid() ||
      status.st_nlink != 1 || status.st_size <= 0 || status.st_size > 2048)
    throw std::runtime_error("Invalid cooperative output journal");
  std::string value(static_cast<std::size_t>(status.st_size), '\0');
  std::size_t received = 0;
  while (received < value.size()) {
    const auto size =
        read(input.value, value.data() + received, value.size() - received);
    if (size < 0 && errno == EINTR)
      continue;
    if (size <= 0)
      throw std::runtime_error("Truncated cooperative output journal");
    received += static_cast<std::size_t>(size);
  }
  return parse_json(value, 2048);
}
} // namespace
bool cooperative_agent_kind(const std::string &kind) {
  return kind == "JAVA_CPU" || kind == "JAVA_HEAP" || kind == "GPERF_CPU" ||
         kind == "GPERF_HEAP" || kind == "JEPROF_HEAP";
}
CooperativeFiles::CooperativeFiles(const Catalog &catalog, fs::path root,
                                   std::string agent, std::uint64_t maximum,
                                   bool bounded_filesystem)
    : catalog_(catalog), root_(std::move(root)), agent_(std::move(agent)),
      prefix_("agent-" + agent_ + "-"), bounded_(bounded_filesystem),
      maximum_(maximum) {
  struct stat status {};
  if (!safe_identifier(agent_) || !root_.is_absolute() ||
      root_ == root_.root_path() || lstat(root_.c_str(), &status) ||
      !S_ISDIR(status.st_mode) ||
      (status.st_uid != 0 && status.st_uid != geteuid()) ||
      (status.st_mode & 0007) || maximum < 128ULL * 1024 * 1024 ||
      maximum > 8ULL * 1024 * 1024 * 1024 ||
      (bounded_filesystem && fs::space(root_).capacity > maximum))
    throw std::runtime_error("Cooperative RAW requires the configured "
                             "protected bounded receive filesystem");
}
void CooperativeFiles::prepare_native_control(const fs::path &directory) const {
  if (!directory.is_absolute() || directory == directory.root_path())
    throw std::invalid_argument(
        "Native control root must be an absolute protected directory");
  struct stat status {
  }, receive{};
  if (lstat(directory.c_str(), &status)) {
    if (errno != ENOENT ||
        fs::canonical(directory.parent_path()) != fs::canonical(root_) ||
        !fs::create_directory(directory) || chmod(directory.c_str(), 0700) ||
        lstat(directory.c_str(), &status))
      throw std::runtime_error("Native control root cannot be safely created "
                               "inside the receive root");
    directory_sync(root_);
  }
  if (!S_ISDIR(status.st_mode) || status.st_uid != geteuid() ||
      (status.st_mode & 0777) != 0700 || lstat(root_.c_str(), &receive) ||
      status.st_dev != receive.st_dev ||
      (bounded_ && fs::space(directory).capacity > maximum_))
    throw std::runtime_error("Native control scratch must be private and on "
                             "the bounded receive filesystem");
}
fs::path CooperativeFiles::prepare(const fs::path &journal,
                                   const std::string &kind,
                                   std::int64_t attempt) const {
  if (!cooperative_agent_kind(kind) || attempt <= 0 ||
      journal.filename() != "attempt-" + std::to_string(attempt) ||
      fs::exists(journal / "cooperative-output.json"))
    throw std::invalid_argument("Invalid cooperative capture identity");
  std::array<unsigned char, 32> entropy{};
  if (RAND_bytes(entropy.data(), entropy.size()) != 1)
    throw std::runtime_error("Cannot allocate cooperative capture identity");
  static constexpr char alphabet[] = "0123456789abcdef";
  std::string token;
  for (const auto byte : entropy) {
    token += alphabet[byte >> 4];
    token += alphabet[byte & 15];
  }
  const auto output = root_ / (prefix_ + token);
  if (!fs::create_directory(output) || chmod(output.c_str(), 0700))
    throw std::runtime_error("Cooperative receive directory unavailable");
  try {
    const auto content = json{{"schema_version", 1},
                              {"token", token},
                              {"agent_id", agent_},
                              {"kind", kind},
                              {"attempt_id", attempt}};
    atomic_write((journal / "cooperative-output.json").string(),
                 content.dump());
    directory_sync(root_);
  } catch (...) {
    std::error_code ignored;
    fs::remove_all(output, ignored);
    throw;
  }
  return output;
}
fs::path CooperativeFiles::directory(const fs::path &journal) const {
  return locate(journal, true);
}
fs::path CooperativeFiles::locate(const fs::path &journal,
                                  bool required) const {
  const auto saved = marker(journal);
  if (!saved.is_object() || saved.size() != 5 ||
      saved.value("schema_version", 0) != 1 ||
      (saved.value("token", "").size() != 64 ||
       saved.value("token", "").find_first_not_of("0123456789abcdef") !=
           std::string::npos) ||
      saved.value("agent_id", "") != agent_ ||
      !cooperative_agent_kind(saved.value("kind", "")) ||
      !saved.at("attempt_id").is_number_integer() ||
      saved.at("attempt_id").get<std::int64_t>() <= 0 ||
      journal.filename() !=
          "attempt-" +
              std::to_string(saved.at("attempt_id").get<std::int64_t>()))
    throw std::runtime_error("Cooperative output journal identity mismatch");
  const auto output = root_ / (prefix_ + saved.at("token").get<std::string>());
  struct stat status {};
  if (lstat(output.c_str(), &status)) {
    if (!required && errno == ENOENT)
      return output;
    throw std::runtime_error("Cooperative output directory unavailable");
  }
  if (!S_ISDIR(status.st_mode) || status.st_uid != geteuid() ||
      (status.st_mode & 0777) != 0700)
    throw std::runtime_error("Cooperative output directory ownership changed");
  return output;
}
fs::path CooperativeFiles::raw(const fs::path &journal,
                               const std::string &name) const {
  const auto saved = marker(journal);
  if (name != catalog_.get(saved.at("kind").get<std::string>())
                  .at("raw_name")
                  .get<std::string>())
    throw std::invalid_argument(
        "Cooperative output filename is not the fixed catalog RAW");
  return directory(journal) / name;
}
void CooperativeFiles::sync(const fs::path &journal, const std::string &name,
                            std::uint64_t maximum) const {
  const auto path = raw(journal, name);
  Descriptor input{
      open(path.c_str(), O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC)};
  struct stat status {};
  if (input.value < 0 || fstat(input.value, &status) ||
      !S_ISREG(status.st_mode) || status.st_uid != geteuid() ||
      status.st_nlink != 1 || status.st_size <= 0 ||
      static_cast<std::uint64_t>(status.st_size) > maximum ||
      fsync(input.value))
    throw std::runtime_error(
        "Cooperative RAW is not a bounded owned regular file");
  directory_sync(path.parent_path());
}
void CooperativeFiles::remove(const fs::path &journal) const {
  if (!fs::exists(journal / "cooperative-output.json"))
    return;
  const auto output = locate(journal, false);
  fs::remove_all(output);
  directory_sync(root_);
  fs::remove(journal / "cooperative-output.json");
  directory_sync(journal);
}
std::uint64_t CooperativeFiles::bytes(std::uint64_t maximum) const {
  std::uint64_t total = 0, entries = 0;
  for (const auto &entry : fs::directory_iterator(root_)) {
    if (!entry.path().filename().string().starts_with(prefix_))
      continue;
    struct stat status {};
    if (++entries > 10000 || lstat(entry.path().c_str(), &status) ||
        !S_ISDIR(status.st_mode) || status.st_uid != geteuid())
      return maximum;
    for (const auto &file : fs::directory_iterator(entry)) {
      if (++entries > 100000 || lstat(file.path().c_str(), &status) ||
          !S_ISREG(status.st_mode) || status.st_uid != geteuid() ||
          status.st_nlink != 1 || status.st_size < 0 ||
          static_cast<std::uint64_t>(status.st_size) > maximum - total)
        return maximum;
      total += static_cast<std::uint64_t>(status.st_size);
    }
  }
  return total;
}
unsigned CooperativeFiles::sweep(const fs::path &journals,
                                 std::uint64_t retention_seconds) const {
  if (retention_seconds < 300 || retention_seconds > 604800)
    throw std::invalid_argument("Invalid cooperative retention bound");
  std::set<std::string> referenced;
  unsigned visited = 0, removed = 0;
  for (const auto &journal : fs::directory_iterator(journals)) {
    if (++visited > 10000)
      throw std::runtime_error("Cooperative journal scan exceeds its bound");
    if (journal.is_symlink() || !journal.is_directory() ||
        !fs::exists(journal.path() / "cooperative-output.json"))
      continue;
    // A corrupt journal prevents deleting uncertain ownership references.
    const auto saved = marker(journal.path());
    if (saved.value("agent_id", "") != agent_ ||
        saved.value("token", "").size() != 64 ||
        saved.value("token", "").find_first_not_of("0123456789abcdef") !=
            std::string::npos)
      throw std::runtime_error("Cooperative retention journal is corrupt");
    referenced.insert(prefix_ + saved.at("token").get<std::string>());
  }
  const auto cutoff = fs::file_time_type::clock::now() -
                      std::chrono::seconds(retention_seconds);
  visited = 0;
  for (const auto &entry : fs::directory_iterator(root_)) {
    if (++visited > 10000)
      throw std::runtime_error("Cooperative receive scan exceeds its bound");
    const auto name = entry.path().filename().string();
    if (!name.starts_with(prefix_) || name.size() != prefix_.size() + 64 ||
        referenced.contains(name) ||
        name.substr(prefix_.size()).find_first_not_of("0123456789abcdef") !=
            std::string::npos)
      continue;
    struct stat status {};
    if (lstat(entry.path().c_str(), &status) || !S_ISDIR(status.st_mode) ||
        status.st_uid != geteuid() ||
        fs::last_write_time(entry.path()) >= cutoff)
      continue;
    bool active = false;
    unsigned files = 0;
    for (const auto &file : fs::directory_iterator(entry)) {
      if (++files > 64 || file.is_symlink() || !file.is_regular_file() ||
          fs::last_write_time(file.path()) >= cutoff) {
        active = true;
        break;
      }
    }
    if (!active) {
      fs::remove_all(entry.path());
      ++removed;
    }
  }
  if (removed)
    directory_sync(root_);
  return removed;
}
} // namespace dropcore
