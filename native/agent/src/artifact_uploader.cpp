#include "artifact_uploader.h"

#include <sys/wait.h>
#include <sys/stat.h>
#include <unistd.h>

#include <array>
#include <chrono>
#include <vector>

namespace mini_drop_native {
namespace {

std::string curl_config_escape(const std::string& value) {
  std::string escaped;
  escaped.reserve(value.size());
  for (const char ch : value) {
    if (ch == '\\' || ch == '"') escaped.push_back('\\');
    escaped.push_back(ch);
  }
  return escaped;
}

bool write_all(int fd, const std::string& value) {
  std::size_t offset = 0;
  while (offset < value.size()) {
    const auto count = ::write(fd, value.data() + offset, value.size() - offset);
    if (count <= 0) return false;
    offset += static_cast<std::size_t>(count);
  }
  return true;
}

bool run_simple_command(const std::vector<std::string>& argv) {
  const pid_t child = ::fork();
  if (child < 0) return false;
  if (child == 0) {
    std::vector<char*> args;
    args.reserve(argv.size() + 1);
    for (const auto& item : argv) {
      args.push_back(const_cast<char*>(item.c_str()));
    }
    args.push_back(nullptr);
    ::execvp(args[0], args.data());
    _exit(127);
  }
  int status = 0;
  return ::waitpid(child, &status, 0) == child &&
         WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

}  // namespace

std::string sha256_file(const std::filesystem::path& local_path) {
  int output_pipe[2];
  if (::pipe(output_pipe) != 0) return {};
  const pid_t child = ::fork();
  if (child < 0) {
    ::close(output_pipe[0]);
    ::close(output_pipe[1]);
    return {};
  }
  if (child == 0) {
    ::dup2(output_pipe[1], STDOUT_FILENO);
    ::close(output_pipe[0]);
    ::close(output_pipe[1]);
    ::execlp("sha256sum", "sha256sum", local_path.c_str(), nullptr);
    _exit(127);
  }
  ::close(output_pipe[1]);
  std::array<char, 256> buffer{};
  const auto count = ::read(output_pipe[0], buffer.data(), buffer.size() - 1);
  ::close(output_pipe[0]);
  int status = 0;
  if (::waitpid(child, &status, 0) != child || !WIFEXITED(status) ||
      WEXITSTATUS(status) != 0 || count < 64) {
    return {};
  }
  const std::string digest(buffer.data(), 64);
  return digest.find_first_not_of("0123456789abcdef") == std::string::npos
      ? digest
      : std::string{};
}

std::string authorized_object_key(
    const Task& task, const std::string& filename) {
  if (filename.empty() || filename.find('/') != std::string::npos ||
      filename.find('\\') != std::string::npos) {
    return {};
  }
  const std::string suffix = "/" + filename;
  std::string match;
  for (const auto& [object_key, target] : task.upload_targets) {
    if (target.put_url.empty() || object_key.size() < suffix.size() ||
        object_key.compare(object_key.size() - suffix.size(), suffix.size(), suffix) != 0) {
      continue;
    }
    if (!match.empty()) return {};
    match = object_key;
  }
  return match;
}

bool upload_artifact(
    const Task& task,
    const std::filesystem::path& local_path,
    const std::string& object_key,
    std::string& error) {
  const auto target = task.upload_targets.find(object_key);
  if (target == task.upload_targets.end() || target->second.put_url.empty()) {
    error = "no task-scoped upload authorization for artifact object";
    return false;
  }
  const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::system_clock::now().time_since_epoch()).count();
  if (target->second.expires_unix_ms <= now_ms) {
    error = "task-scoped upload authorization expired";
    return false;
  }

  // Keep the signed URL out of argv/process listings. curl reads a mode-0600
  // one-shot config file which is unlinked immediately after the child exits.
  std::array<char, 40> config_path{};
  const std::string pattern = "/tmp/mini-drop-upload-XXXXXX";
  std::copy(pattern.begin(), pattern.end(), config_path.begin());
  int fd = ::mkstemp(config_path.data());
  if (fd < 0 || ::fchmod(fd, S_IRUSR | S_IWUSR) != 0) {
    if (fd >= 0) ::close(fd);
    error = "could not create protected upload configuration";
    return false;
  }
  const std::string curl_config =
      std::string("fail\n" "silent\n" "show-error\n") +
      // Bounded recovery for transient DNS/connect/5xx failures.  curl keeps
      // the exact PUT request and signed URL; it never starts a new sample.
      "retry = 2\n" "retry-all-errors\n" "retry-delay = 1\n" +
      "connect-timeout = 10\n" +
      std::string("upload-file = \"") +
      curl_config_escape(local_path.string()) + "\"\n" +
      "url = \"" + curl_config_escape(target->second.put_url) + "\"\n";
  const bool config_written = write_all(fd, curl_config);
  ::close(fd);
  const bool uploaded = config_written &&
      run_simple_command({"curl", "--config", config_path.data()});
  ::unlink(config_path.data());
  if (!uploaded) {
    error = "artifact upload with task-scoped authorization failed";
    return false;
  }
  return true;
}

}  // namespace mini_drop_native
