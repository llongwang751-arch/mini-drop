// 为具体采集动作提供统一生命周期，便于不同工具共享取消、超时与目标复核。
// 调用顺序是Validate → Prepare → Start → Poll → Collect，退出路径最终执行Cleanup。
// 工作线程的异常通过future传给调用方；本地runner.state用于解释执行阶段，不替代数据库状态。
// Cleanup只清理可丢弃中间文件，不能在Server确认前删除RAW与恢复记录。

#include "drop/artifact.hpp"
#include "drop/runner.hpp"
#include <atomic>
#include <future>
#include <sys/stat.h>
#include <thread>

namespace dropcore {
namespace {
class ManagedRunner final : public Runner {
public:
  ManagedRunner(RunnerAction validate, RunnerAction prepare,
                std::function<RunnerOutput(const RunnerContext &)> collect)
      : validate_(std::move(validate)), prepare_(std::move(prepare)),
        collect_(std::move(collect)) {}
  ~ManagedRunner() override { Cleanup(); }
  void Validate(const RunnerContext &c) const override { validate_(c); }
  void Prepare(const RunnerContext &c) override {
    if (prepared_)
      throw std::logic_error("Runner already prepared");
    prepare_(c);
    context_ = c;
    atomic_write_state("PREPARED");
    prepared_ = true;
  }
  void Start(const RunnerContext &c) override {
    if (!prepared_ || started_)
      throw std::logic_error("Runner start requires exactly one prepare");
    context_ = c;
    const auto original = c.canceled;
    context_.canceled = [this, original] {
      return stopped_.load() || original();
    };
    future_ = promise_.get_future();
    started_ = true;
    deadline_ = std::chrono::steady_clock::now() +
                std::chrono::seconds(c.duration_seconds + 70);
    atomic_write_state("RUNNING");
    worker_ = std::jthread([this] {
      try {
        promise_.set_value(collect_(context_));
      } catch (...) {
        promise_.set_exception(std::current_exception());
      }
      done_ = true;
    });
  }
  bool Poll() override {
    if (!started_)
      throw std::logic_error("Runner has not started");
    if (done_)
      return true;
    if (context_.canceled()) {
      Stop();
      throw std::runtime_error("TASK_CANCELED");
    }
    if (std::chrono::steady_clock::now() > deadline_) {
      Stop();
      throw std::runtime_error("RUNNER_TIMEOUT");
    }
    if (context_.target.pid &&
        !same_identity(context_.target,
                       process_identity(context_.target.pid))) {
      Stop();
      throw std::runtime_error("TARGET_CHANGED");
    }
    return false;
  }
  void Stop() noexcept override { stopped_ = true; }
  RunnerOutput Collect() override {
    if (!started_ || !done_ || collected_)
      throw std::logic_error("Collection requires a completed runner");
    collected_ = true;
    auto out = future_.get();
    atomic_write_state("COLLECTED");
    return out;
  }
  void Cleanup() noexcept override {
    Stop();
    if (worker_.joinable())
      worker_.join();
    if (!prepared_)
      return;
    // 只删除当前会话持有且可丢弃的临时文件；主RAW、
    // 附属ELF与Agent恢复记录一直保留到Server确认，避免成功采集后丢数据。
    for (const auto *name : {"trace.raw", "bolt.perf.data", "perf.data",
                             "perf.script", "runtime.log", "detach.log"}) {
      std::error_code error;
      std::filesystem::remove(context_.directory / name, error);
    }
    try {
      atomic_write_state("CLEANED");
    } catch (...) {
    }
    prepared_ = false;
  }

private:
  void atomic_write_state(const char *state) {
    atomic_write((context_.directory / "runner.state").string(),
                 std::string(state) + "\n");
  }
  RunnerAction validate_, prepare_;
  std::function<RunnerOutput(const RunnerContext &)> collect_;
  RunnerContext context_;
  std::promise<RunnerOutput> promise_;
  std::future<RunnerOutput> future_;
  std::jthread worker_;
  std::atomic<bool> stopped_{false}, done_{false};
  bool prepared_ = false, started_ = false, collected_ = false;
  std::chrono::steady_clock::time_point deadline_;
};
} // 结束匿名命名空间
std::unique_ptr<Runner> make_managed_runner(
    RunnerAction validate, RunnerAction prepare,
    std::function<RunnerOutput(const RunnerContext &)> collect) {
  return std::make_unique<ManagedRunner>(
      std::move(validate), std::move(prepare), std::move(collect));
}
} // 结束 dropcore 命名空间
