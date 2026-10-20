// 显式导入的确定性采集器数据，仅用于IPC故障测试。
// 此可执行文件不安装进发布镜像，其通过不构成现场采样证据。
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

namespace {
void append(std::string &output, std::uint64_t value, unsigned bytes) {
  for (unsigned index = 0; index < bytes; ++index)
    output.push_back(static_cast<char>(value >> (8 * index)));
}
std::string imported_perf_mapping(int pid) {
  const auto executable = "/proc/" + std::to_string(pid) + "/exe";
  struct stat file {};
  if (stat(executable.c_str(), &file))
    throw std::runtime_error("Imported mapping target unavailable");
  const auto target = std::filesystem::read_symlink(executable).string();
  std::string body;
  append(body, pid, 4);
  append(body, pid, 4);
  append(body, 0x400000, 8);
  append(body, 4096, 8);
  append(body, 0, 8);
  append(body, major(file.st_dev), 4);
  append(body, minor(file.st_dev), 4);
  append(body, file.st_ino, 8);
  append(body, 0, 8);
  append(body, 5, 4);
  append(body, 2, 4);
  body += target;
  body.push_back(0);
  while (body.size() % 8)
    body.push_back(0);
  std::string data;
  append(data, 10, 4);
  append(data, 2, 2);
  append(data, 8 + body.size(), 2);
  data += body;
  std::string header = "PERFILE2";
  append(header, 104, 8);
  append(header, 0, 8);
  append(header, 104, 8);
  append(header, 0, 8);
  append(header, 104, 8);
  append(header, data.size(), 8);
  header.resize(104, 0);
  return header + data;
}
} // 结束匿名命名空间
int main(int argc, char **argv) {
  const std::string mode = std::filesystem::path(argv[0]).filename();
  if (argc == 2 && (std::string(argv[1]) == "version" ||
                    std::string(argv[1]) == "--version" ||
                    std::string(argv[1]) == "-version")) {
    std::cout << "Imported helper IPC fixture 1.0\n";
    return 0;
  }
  if (mode == "helper-fixture-perf2bolt") {
    std::filesystem::path input;
    for (int index = 1; index + 1 < argc; ++index)
      if (std::string(argv[index]) == "-p")
        input = argv[index + 1];
    // 导入的聚合器必须像真实工具一样检查前置执行依赖，
    // 否则过早调用也可能让IPC测试假通过。
    if (input.empty() || !std::filesystem::is_regular_file(input) ||
        !std::filesystem::is_regular_file(input.string() +
                                           ".imported-buildid"))
      return 2;
    for (int i = 1; i + 1 < argc; ++i)
      if (std::string(argv[i]) == "-o") {
        std::ofstream(argv[i + 1])
            << "no_lbr cycles:u\n1 1 fixture 0 1 fixture 1 1\n";
        return 0;
      }
    return 2;
  }
  if (argc > 1 && std::string(argv[1]) == "buildid-list") {
    const std::filesystem::path input(argv[argc - 1]);
    const auto identity = input.string() + ".imported-buildid";
    if (std::filesystem::exists(identity))
      std::cout << std::ifstream(identity).rdbuf();
    else if (input.string().starts_with("/proc/") &&
             input.filename() == "exe")
      std::cout << std::string(39, '0') << "1\n";
    return 0;
  }
  if (argc > 1 && std::string(argv[1]) == "inject") {
    std::filesystem::path input, output;
    std::string known;
    for (int index = 2; index + 1 < argc; ++index) {
      if (std::string(argv[index]) == "-i")
        input = argv[index + 1];
      if (std::string(argv[index]) == "-o")
        output = argv[index + 1];
      if (std::string(argv[index]) == "--known-build-ids")
        known = argv[index + 1];
    }
    if (input.empty() || output.empty() || known.empty())
      return 2;
    std::filesystem::copy_file(input, output);
    std::ofstream(output.string() + ".imported-buildid") << known << '\n';
    return 0;
  }
  if (argc > 1 && std::string(argv[1]) == "script") {
    std::cout << "imported-ipc-fixture 123/123 1.000000001: cpu-clock:\n       "
                 " 000001 imported_frame (/imported-fixture)\n\n";
    return 0;
  }
  if (argc > 1 && std::string(argv[1]) == "record") {
    std::filesystem::path output;
    int pid = 0;
    for (int i = 1; i + 1 < argc; ++i)
      if (std::string(argv[i]) == "-o")
        output = argv[i + 1];
      else if (std::string(argv[i]) == "-p")
        pid = std::stoi(argv[i + 1]);
    if (output.empty() || pid <= 1)
      return 2;
    std::ofstream(output.parent_path() / "fixture-captures.log", std::ios::app)
        << "imported record invocation\n";
    std::this_thread::sleep_for(
        std::chrono::milliseconds(1000 * std::stoi(argv[argc - 1])));
    std::ofstream(output, std::ios::binary) << imported_perf_mapping(pid);
    return 0;
  }
  return 2;
}
