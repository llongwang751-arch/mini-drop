# 15 分钟演示脚本

## 0:00 - 2:00 项目与架构

1. 说明目标：替代人工 SSH 上机执行性能工具，把采集、分析、展示和审计串成平台。
2. 打开 `DESIGN.md` 的架构图与状态机图。
3. 执行 `docker compose up --build -d`，打开 <http://localhost:8080>。

## 2:00 - 6:00 端到端 perf

1. 在 Agent 可见的 Linux 环境启动 CPU 密集目标进程并记录 PID。
2. Web 新建 `CPU / perf` 任务，输入 PID、3 秒、49Hz。
3. 展示状态依次经过 PENDING、RUNNING、UPLOADING、DONE。
4. 打开详情，展示真实 backend、火焰图、TopN、采样时序和每次状态迁移 reason。

## 6:00 - 9:00 eBPF 与语言级采集

1. 执行 `dd if=/dev/zero of=/tmp/minidrop-io.bin bs=1M count=512`。
2. 创建 eBPF 任务，展示 `kprobe:vfs_write` 的写入进程分布变化。
3. 对 Python 目标创建 py-spy 任务，展示独立的 Python 语言调用栈视图。

## 9:00 - 11:00 Continuous Profiling

1. 创建勾选“持续性能采集”的短任务。
2. 展示自动生成的多个切片。
3. 设置开始、结束时间查询窗口，点击切片回看结果。
4. 调用停止持续采样 API，说明如何避免无限续建。

## 11:00 - 13:00 工程质量与加分项

1. 执行 `make coverage`，展示测试通过和覆盖率高于 50%。
2. 演示自然语言请求：`对 PID 1234 做 8 秒 py-spy 采集，频率 77Hz`。
3. 说明系统会生成可验证计划；缺少 PID 时明确拒绝，不猜测目标。

## 13:00 - 15:00 最得意的设计与重做思路

最得意的设计是“Server 是唯一任务真相源”：状态迁移合法性、reason 和历史记录在同一事务中完成，因此 Agent 重试或失败不会让 Web 展示虚假状态。

如果重做，我会先把任务事件建模为可重放事件流，再让 Analyzer 通过持久队列异步消费；同时增加 Agent 身份认证、对象存储、符号服务、采集限流和最小权限 eBPF 探针目录。
