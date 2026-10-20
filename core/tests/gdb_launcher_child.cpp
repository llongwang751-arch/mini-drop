// Imported deterministic tool double: proves launcher policy, not GDB capture.
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <linux/openat2.h>
#include <string>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>
int main(int argc, char **argv) {
  if (argc != 8 || prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1)
    return 2;
  const std::string process = std::string("/proc/") + argv[7];
  if (std::string(argv[1]) != "-iex=set auto-load off" ||
      std::string(argv[2]) != "-iex=set sysroot " + process + "/root" ||
      std::string(argv[3]) != "--exec=" + process + "/exe" ||
      std::string(argv[4]) != "--symbols=" + process + "/exe" ||
      (std::string(argv[6]) != "-p" && std::string(argv[6]) != "--pid"))
    return 10;
  const std::string source = argv[5];
  if (!source.starts_with("-x="))
    return 11;
  const auto payload = source.substr(3);
  const int readonly = open(payload.c_str(), O_RDONLY | O_CLOEXEC);
  if (readonly < 0)
    return 3;
  close(readonly);
  const int writeonly = open(payload.c_str(), O_WRONLY | O_CLOEXEC);
  if (writeonly < 0)
    return 4;
  close(writeonly);
  const int readwrite = open(payload.c_str(), O_RDWR | O_CLOEXEC);
  if (readwrite >= 0 || errno != EACCES)
    return 5;
  const int relative = openat(AT_FDCWD, payload.c_str(), O_RDWR | O_CLOEXEC);
  if (relative >= 0 || errno != EACCES)
    return 6;
  const int self = open("/proc/self/mem", O_RDWR | O_CLOEXEC);
  if (self >= 0 || errno != EACCES)
    return 7;
#ifdef __NR_openat2
  open_how how{};
  how.flags = O_RDONLY;
  if (syscall(__NR_openat2, AT_FDCWD, payload.c_str(), &how, sizeof(how)) >=
          0 ||
      errno != EACCES)
    return 8;
#endif
  constexpr char success[] = "launcher filter passed\n";
  return write(STDOUT_FILENO, success, std::strlen(success)) ==
                 static_cast<ssize_t>(std::strlen(success))
             ? 0
             : 9;
}
