// 受信GDB启动器：在执行嵌套工具前固定文件描述符、工具SHA和数字目标身份。
// 通过受限系统调用策略收窄GDB能操作的对象，防止“允许诊断工具”变成任意ptrace入口。
// 此进程是采集权限边界的一部分，不接受用户直接提供shell命令或可执行路径。

#include <array>
#include <cerrno>
#include <charconv>
#include <climits>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <nlohmann/json.hpp>
#include <openssl/evp.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <vector>

extern char **environ;
namespace {
struct Descriptor {
  int value;
  explicit Descriptor(int fd) : value(fd) {
    if (fd < 0)
      throw std::runtime_error("open");
  }
  ~Descriptor() { close(value); }
};
struct stat ordinary(int fd, std::uint64_t maximum) {
  struct stat status {};
  if (fstat(fd, &status) || !S_ISREG(status.st_mode) || status.st_nlink != 1 ||
      (status.st_mode & 0022) ||
      (status.st_uid != 0 && status.st_uid != geteuid()) ||
      status.st_size <= 0 ||
      static_cast<std::uint64_t>(status.st_size) > maximum)
    throw std::runtime_error("file policy");
  return status;
}
bool unchanged(const struct stat &a, const struct stat &b) {
  return a.st_dev == b.st_dev && a.st_ino == b.st_ino &&
         a.st_size == b.st_size && a.st_mtim.tv_sec == b.st_mtim.tv_sec &&
         a.st_mtim.tv_nsec == b.st_mtim.tv_nsec &&
         a.st_ctim.tv_sec == b.st_ctim.tv_sec &&
         a.st_ctim.tv_nsec == b.st_ctim.tv_nsec;
}
std::string digest(int fd) {
  const auto before = ordinary(fd, 256ULL * 1024 * 1024);
  EVP_MD_CTX *context = EVP_MD_CTX_new();
  if (!context)
    throw std::runtime_error("digest allocation");
  struct Cleanup {
    EVP_MD_CTX *value;
    ~Cleanup() { EVP_MD_CTX_free(value); }
  } cleanup{context};
  if (EVP_DigestInit_ex(context, EVP_sha256(), nullptr) != 1)
    throw std::runtime_error("digest init");
  std::array<char, 65536> buffer{};
  for (;;) {
    const auto count = read(fd, buffer.data(), buffer.size());
    if (count < 0 && errno == EINTR)
      continue;
    if (count < 0)
      throw std::runtime_error("digest read");
    if (!count)
      break;
    if (EVP_DigestUpdate(context, buffer.data(),
                         static_cast<std::size_t>(count)) != 1)
      throw std::runtime_error("digest update");
  }
  std::array<unsigned char, EVP_MAX_MD_SIZE> bytes{};
  unsigned length = 0;
  if (EVP_DigestFinal_ex(context, bytes.data(), &length) != 1 || length != 32 ||
      !unchanged(before, ordinary(fd, 256ULL * 1024 * 1024)))
    throw std::runtime_error("digest finish");
  std::string result;
  constexpr char digits[] = "0123456789abcdef";
  for (unsigned i = 0; i < length; ++i) {
    result += digits[bytes[i] >> 4];
    result += digits[bytes[i] & 15];
  }
  return result;
}
std::string proc_text(const std::string &path, std::size_t maximum) {
  Descriptor source(
      open(path.c_str(), O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC));
  std::string result;
  std::array<char, 1024> block{};
  for (;;) {
    const auto count = read(source.value, block.data(), block.size());
    if (count < 0 && errno == EINTR)
      continue;
    if (count < 0)
      throw std::runtime_error("proc read");
    if (!count)
      return result;
    if (static_cast<std::size_t>(count) > maximum - result.size())
      throw std::runtime_error("proc bound");
    result.append(block.data(), static_cast<std::size_t>(count));
  }
}
unsigned target_pid(int argc, char **argv) {
  unsigned result = 0;
  for (int i = 1; i < argc; ++i) {
    const std::string argument = argv[i];
    if (argument == "-p" || argument == "--pid") {
      if (result || ++i == argc)
        throw std::runtime_error("duplicate or missing pid");
      const std::string number = argv[i];
      if (number.empty() || number.front() == '0' ||
          number.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("pid syntax");
      const auto converted =
          std::from_chars(number.data(), number.data() + number.size(), result);
      if (converted.ec != std::errc{} ||
          converted.ptr != number.data() + number.size() || result <= 1 ||
          result > INT_MAX || result == static_cast<unsigned>(getpid()))
        throw std::runtime_error("pid bound");
    } else if (argument.empty() || argument.front() != '-') {
      throw std::runtime_error("positional target override");
    } else if (argument.starts_with("-p") || argument.starts_with("--p")) {
      throw std::runtime_error("unsupported pid option");
    } else if (argument == "-e" || argument == "-s" || argument == "-se" ||
               argument == "-c" || argument.starts_with("--exec") ||
               argument.starts_with("--symbols") ||
               argument.starts_with("--se") || argument.starts_with("--core") ||
               argument == "--args") {
      throw std::runtime_error("target file override");
    }
  }
  if (!result)
    throw std::runtime_error("missing pid");
  return result;
}
std::uint64_t identity_number(const nlohmann::json &value, const char *key,
                              std::uint64_t minimum, std::uint64_t maximum) {
  const auto &number = value.at(key);
  if (!number.is_number_integer() ||
      (number.is_number_integer() && !number.is_number_unsigned() &&
       number.get<std::int64_t>() < 0))
    throw std::runtime_error("target number");
  const auto result = number.get<std::uint64_t>();
  if (result < minimum || result > maximum)
    throw std::runtime_error("target bound");
  return result;
}
nlohmann::json expected_identity(unsigned pid) {
  const char *configured = std::getenv("DROP_GDB_TARGET_IDENTITY");
  if (!configured || strnlen(configured, 4097) > 4096)
    throw std::runtime_error("target metadata");
  const auto value = nlohmann::json::parse(configured);
  if (!value.is_object() || value.size() != 7 ||
      identity_number(value, "pid", 2, INT_MAX) != pid)
    throw std::runtime_error("target pid");
  (void)identity_number(value, "uid", 0, UINT_MAX);
  (void)identity_number(value, "start_time_ticks", 1, INT64_MAX);
  (void)identity_number(value, "namespace_pid", 1, INT_MAX);
  (void)identity_number(value, "executable_device", 1, INT64_MAX);
  (void)identity_number(value, "executable_inode", 1, INT64_MAX);
  const auto name = value.at("pid_namespace").get<std::string>();
  if (name.size() < 7 || name.size() > 64 || !name.starts_with("pid:[") ||
      name.back() != ']')
    throw std::runtime_error("target namespace");
  return value;
}
void validate_target_identity(unsigned pid, const nlohmann::json &expected) {
  const auto base = "/proc/" + std::to_string(pid);
  const auto process = proc_text(base + "/stat", 8192);
  const auto end = process.rfind(')');
  if (end == std::string::npos)
    throw std::runtime_error("target stat");
  std::istringstream fields(process.substr(end + 2));
  std::string field;
  for (unsigned i = 0; i <= 19; ++i)
    if (!(fields >> field))
      throw std::runtime_error("target stat fields");
  std::uint64_t started = 0;
  const auto parsed =
      std::from_chars(field.data(), field.data() + field.size(), started);
  if (parsed.ec != std::errc{} || parsed.ptr != field.data() + field.size() ||
      expected.at("start_time_ticks") != started)
    throw std::runtime_error("target replaced");
  std::istringstream status(proc_text(base + "/status", 32768));
  std::string line;
  unsigned uid = UINT_MAX, namespace_pid = pid;
  while (std::getline(status, line)) {
    if (line.starts_with("Uid:")) {
      std::istringstream values(line.substr(4));
      if (!(values >> uid))
        throw std::runtime_error("target uid");
    }
    if (line.starts_with("NSpid:")) {
      std::istringstream values(line.substr(6));
      unsigned item;
      while (values >> item)
        namespace_pid = item;
    }
  }
  std::array<char, 65> link{};
  const auto count =
      readlink((base + "/ns/pid").c_str(), link.data(), link.size());
  if (count <= 0 || count >= static_cast<ssize_t>(link.size()) ||
      expected.at("pid_namespace") !=
          std::string(link.data(), static_cast<std::size_t>(count)) ||
      expected.at("uid") != uid ||
      expected.at("namespace_pid") != namespace_pid)
    throw std::runtime_error("target identity");
  Descriptor executable(open((base + "/exe").c_str(), O_RDONLY | O_CLOEXEC));
  Descriptor root(
      open((base + "/root").c_str(), O_PATH | O_DIRECTORY | O_CLOEXEC));
  struct stat file {
  }, directory{};
  if (fstat(executable.value, &file) || !S_ISREG(file.st_mode) ||
      fstat(root.value, &directory) || !S_ISDIR(directory.st_mode) ||
      expected.at("executable_device") !=
          static_cast<std::uint64_t>(file.st_dev) ||
      expected.at("executable_inode") !=
          static_cast<std::uint64_t>(file.st_ino))
    throw std::runtime_error("target executable");
}
void ptrace_only() {
#if defined(__x86_64__)
  constexpr unsigned architecture = AUDIT_ARCH_X86_64;
#elif defined(__aarch64__)
  constexpr unsigned architecture = AUDIT_ARCH_AARCH64;
#else
#error Unsupported GDB launcher architecture
#endif
  std::vector<sock_filter> filter{
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, arch)),
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, architecture, 1, 0),
      BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, nr))};
#if defined(__x86_64__)
  filter.push_back(BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, 0x40000000U, 0, 1));
  filter.push_back(BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS));
#endif
  const auto deny_readwrite = [&](unsigned call, unsigned argument) {
    filter.push_back(BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, call, 0, 4));
    filter.push_back(
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
                 static_cast<std::uint32_t>(offsetof(seccomp_data, args) +
                                            argument * sizeof(std::uint64_t))));
    filter.push_back(BPF_STMT(BPF_ALU | BPF_AND | BPF_K, O_ACCMODE));
    filter.push_back(BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, O_RDWR, 0, 1));
    filter.push_back(BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EACCES));
    filter.push_back(
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, nr)));
  };
#ifdef __NR_open
  deny_readwrite(__NR_open, 1);
#endif
  deny_readwrite(__NR_openat, 2);
#ifdef __NR_openat2
  filter.push_back(BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_openat2, 0, 1));
  filter.push_back(BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EACCES));
#endif
  filter.push_back(BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW));
  sock_fprog program{static_cast<unsigned short>(filter.size()), filter.data()};
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) ||
      prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program, 0, 0))
    throw std::runtime_error("seccomp installation");
}
} // 结束匿名命名空间
int main(int argc, char **argv) {
  try {
    if (argc > 128)
      throw std::runtime_error("argument count");
    std::size_t total = 0;
    for (int i = 1; i < argc; ++i)
      if ((total += std::string(argv[i]).size()) > 65536)
        throw std::runtime_error("argument bytes");
    const auto pid = target_pid(argc, argv);
    const auto identity = expected_identity(pid);
    validate_target_identity(pid, identity);
    const char *configured = std::getenv("DROP_GDB_MANIFEST_FILE");
    const std::string manifest =
        configured ? configured : "/opt/drop-tools/manifest.json";
    if (manifest.empty() || manifest.front() != '/' || manifest.size() > 4096)
      throw std::runtime_error("manifest path");
    Descriptor source(
        open(manifest.c_str(), O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC));
    const auto before = ordinary(source.value, 1024 * 1024);
    std::string text(static_cast<std::size_t>(before.st_size), '\0');
    if (read(source.value, text.data(), text.size()) !=
            static_cast<ssize_t>(text.size()) ||
        !unchanged(before, ordinary(source.value, 1024 * 1024)))
      throw std::runtime_error("manifest read");
    const auto entry = nlohmann::json::parse(text).at("tools").at("gdb");
    const auto path = entry.at("path").get<std::string>();
    if (path.empty() || path.front() != '/' || path.size() > 4096 ||
        path.find('\0') != std::string::npos)
      throw std::runtime_error("gdb path");
    Descriptor binary(
        open(path.c_str(), O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC));
    if (!(ordinary(binary.value, 256ULL * 1024 * 1024).st_mode & 0111) ||
        digest(binary.value) != entry.at("sha256").get<std::string>())
      throw std::runtime_error("gdb integrity");
    std::array<char, 4097> self_path{};
    const auto self_size =
        readlink("/proc/self/exe", self_path.data(), self_path.size() - 1);
    if (self_size <= 0 || self_size >= 4096)
      throw std::runtime_error("launcher path");
    Descriptor self(open("/proc/self/exe", O_RDONLY | O_CLOEXEC));
    if (digest(self.value) !=
        entry.at("support_files")
            .at(std::string(self_path.data(),
                            static_cast<std::size_t>(self_size)))
            .get<std::string>())
      throw std::runtime_error("launcher integrity");
    const auto process = "/proc/" + std::to_string(pid);
    std::vector<std::string> fixed{
        path, "-iex=set auto-load off", "-iex=set sysroot " + process + "/root",
        "--exec=" + process + "/exe", "--symbols=" + process + "/exe"};
    std::vector<char *> arguments;
    for (auto &item : fixed)
      arguments.push_back(item.data());
    for (int i = 1; i < argc; ++i)
      arguments.push_back(argv[i]);
    arguments.push_back(nullptr);
    ptrace_only();
    validate_target_identity(pid, identity);
    fexecve(binary.value, arguments.data(), environ);
  } catch (...) {
  }
  constexpr char failure[] = "DROP_GDB_LAUNCHER_POLICY_DENIED\n";
  const auto logged = write(STDERR_FILENO, failure, sizeof(failure) - 1);
  return logged < 0 ? 127 : 126;
}
