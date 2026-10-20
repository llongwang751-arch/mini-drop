// 明确标记的玩具负载：产生文件I/O、malloc/free和mmap缺页，供真实内核追踪验证。
#include <atomic>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <sys/mman.h>
#include <unistd.h>
std::atomic<bool> running = true;
void stop(int) { running = false; }
int main() {
  std::signal(SIGTERM, stop);
  const auto *directory = std::getenv("DROP_IO_FIXTURE_DIRECTORY");
  const auto path = std::string(directory ? directory : "/tmp") +
                    "/drop-io-fixture-" + std::to_string(getpid());
  auto file = open(path.c_str(), O_CREAT | O_RDWR | O_TRUNC | O_DIRECT, 0600);
  if (file < 0)
    file = open(path.c_str(), O_CREAT | O_RDWR | O_TRUNC, 0600);
  if (file < 0)
    return 2;
  alignas(4096) char buffer[4096]{};
  std::cout << getpid() << std::endl;
  int status = 0;
  while (running) {
    auto *memory = static_cast<char *>(std::malloc(65536));
    if (memory) {
      std::memset(memory, 1, 65536);
      buffer[0] = memory[0];
      std::free(memory);
    }
    auto *mapped = mmap(nullptr, 65536, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mapped != MAP_FAILED) {
      for (int offset = 0; offset < 65536; offset += 4096)
        static_cast<volatile char *>(mapped)[offset] = 2;
      buffer[1] = static_cast<char *>(mapped)[4096];
      if (munmap(mapped, 65536)) {
        status = 3;
        break;
      }
    }
    if (lseek(file, 0, SEEK_SET) < 0 ||
        write(file, buffer, sizeof(buffer)) != sizeof(buffer) || fsync(file) ||
        lseek(file, 0, SEEK_SET) < 0 ||
        read(file, buffer, sizeof(buffer)) != sizeof(buffer)) {
      status = 4;
      break;
    }
    usleep(20000);
  }
  close(file);
  unlink(path.c_str());
  return status;
}
