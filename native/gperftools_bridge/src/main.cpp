// User-space bridge for applications that already opted into gperftools.
// It deliberately does not use ptrace, perf_event_open, sudo or Linux caps.
#include <csignal>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include <sys/stat.h>
#include <unistd.h>

namespace fs = std::filesystem;

struct Options {
  int pid = 0;
  int signal_number = SIGUSR2;
  int wait_seconds = 15;
  long long max_bytes = 256LL * 1024LL * 1024LL;
  fs::path profile;
  fs::path output;
  bool allow_root = false;
};

std::string json_escape(const std::string& value) {
  std::string out;
  for (char ch : value) {
    if (ch == '\\' || ch == '"') { out.push_back('\\'); out.push_back(ch); }
    else if (ch == '\n') out += "\\n";
    else out.push_back(ch);
  }
  return out;
}

Options parse(int argc, char** argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    auto value = [&]() -> std::string {
      if (++i >= argc) throw std::runtime_error("missing value for " + arg);
      return argv[i];
    };
    if (arg == "--pid") options.pid = std::stoi(value());
    else if (arg == "--profile") options.profile = value();
    else if (arg == "--output") options.output = value();
    else if (arg == "--signal") options.signal_number = std::stoi(value());
    else if (arg == "--wait") options.wait_seconds = std::stoi(value());
    else if (arg == "--max-bytes") options.max_bytes = std::stoll(value());
    else if (arg == "--allow-root") options.allow_root = true;
    else throw std::runtime_error("unknown argument: " + arg);
  }
  if (options.pid <= 1 || options.profile.empty() || options.output.empty())
    throw std::runtime_error("--pid, --profile and --output are required");
  if (!options.profile.is_absolute() || !options.output.is_absolute())
    throw std::runtime_error("profile and output paths must be absolute");
  if (options.signal_number <= 0 || options.signal_number >= NSIG)
    throw std::runtime_error("invalid signal number");
  if (options.wait_seconds < 1 || options.wait_seconds > 300 || options.max_bytes < 1)
    throw std::runtime_error("invalid wait or output limit");
  return options;
}

int main(int argc, char** argv) {
  try {
    const Options options = parse(argc, argv);
    const uid_t current_uid = geteuid();
    if (current_uid == 0 && !options.allow_root)
      throw std::runtime_error("refusing root: run this bridge as the application user");

    const fs::path proc_root = fs::path("/proc") / std::to_string(options.pid);
    struct stat process_stat {};
    if (::stat(proc_root.c_str(), &process_stat) != 0)
      throw std::runtime_error("target process does not exist");
    if (process_stat.st_uid != current_uid)
      throw std::runtime_error("target process is owned by another uid");

    // Resolve the application-visible path through its mount namespace root.
    // Reject '..' segments before constructing the host path.
    for (const auto& part : options.profile)
      if (part == "..") throw std::runtime_error("profile path traversal is not allowed");
    const fs::path source = proc_root / "root" / options.profile.relative_path();

    if (::kill(options.pid, options.signal_number) != 0)
      throw std::runtime_error("failed to signal target process");

    std::uintmax_t previous_size = 0;
    int stable_observations = 0;
    for (int second = 0; second < options.wait_seconds; ++second) {
      std::error_code error;
      const auto size = fs::file_size(source, error);
      if (!error && size > 0) {
        if (size == previous_size) ++stable_observations;
        else stable_observations = 0;
        previous_size = size;
        if (stable_observations >= 1) break;
      }
      std::this_thread::sleep_for(std::chrono::seconds(1));
    }
    if (!fs::is_regular_file(source) || fs::file_size(source) == 0)
      throw std::runtime_error("profile was not produced; verify CPUPROFILE and CPUPROFILESIGNAL");
    if (fs::file_size(source) > static_cast<std::uintmax_t>(options.max_bytes))
      throw std::runtime_error("profile exceeds --max-bytes");

    fs::create_directories(options.output.parent_path());
    fs::copy_file(source, options.output, fs::copy_options::overwrite_existing);
    std::cout << "{\"schema_version\":\"gperftools_bridge.v1\",\"pid\":"
              << options.pid << ",\"uid\":" << current_uid << ",\"size_bytes\":"
              << fs::file_size(options.output) << ",\"output\":\""
              << json_escape(options.output.string()) << "\"}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "{\"error\":\"" << json_escape(error.what()) << "\"}\n";
    return 1;
  }
}
