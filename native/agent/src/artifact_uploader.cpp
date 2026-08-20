#include "artifact_uploader.h"

#include <sys/wait.h>
#include <unistd.h>

#include <array>
#include <vector>

namespace mini_drop_native {
namespace {

std::string normalize_endpoint(std::string endpoint) {
  if (endpoint.rfind("http://", 0) == 0 ||
      endpoint.rfind("https://", 0) == 0) {
    return endpoint;
  }
  return "http://" + endpoint;
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

bool upload_artifact(
    const Config& config,
    const std::filesystem::path& local_path,
    const std::string& object_key,
    std::string& error) {
  const std::string alias = "mini-drop";
  if (!run_simple_command({
          "mc", "alias", "set", alias, normalize_endpoint(config.minio_endpoint),
          config.minio_access, config.minio_secret})) {
    error = "MinIO alias configuration failed";
    return false;
  }
  const std::string target =
      alias + "/" + config.minio_bucket + "/" + object_key;
  if (!run_simple_command({"mc", "cp", local_path.string(), target})) {
    error = "artifact upload to MinIO failed";
    return false;
  }
  return true;
}

}  // namespace mini_drop_native
