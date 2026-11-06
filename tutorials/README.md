# Drop 教程配套实作

阅读入口是 `/book/chapter1/`。第 1–10 章沿正式源码解释，第 11–22 章动手实现、故意触发失败，再交付能复查的记录。正文和代码一起使用：独立参考帮助看懂机制，正式仓库负责真实工程约束。

## 两套可以运行的材料

[`mini-drop/`](mini-drop/README.md) 是四语言小项目。C++ 测自己受控计算的 CPU 计数，Go 提供本地 HTTP、文件 journal 和单 worker，Python 校验字节并产出结果，React 工作台显式连接本地 API。它使用真实自身进程数据，单位为 `cpu_ticks`，允许零；两层树表达进程计账，不是函数栈。启动命令、接口、恢复边界和平台差异见该目录 README。

[`labs/`](labs/) 是十二个机制实验，仅使用 Python 标准库、固定合成输入和独立临时文件。无需启动服务或配置模型。在仓库根目录运行：

```powershell
python -m tutorials.labs --list
python -m tutorials.labs --case lifecycle-outbox
python -m tutorials.labs --all --json
python -m unittest discover -s tutorials/labs -t . -v
```

| case | 对应章节 | 直接观察 |
| --- | --- | --- |
| lifecycle-outbox | 14 | 接收后响应丢失、重复投递和幂等消费 |
| attempt-fence | 14 | 旧轮次取消失败，不能改变当前状态或伪造事件 |
| lease-handover | 14 | 租约到期、接班者新 run 与迟到发布 |
| atomic-events | 14 | 注入失败后状态、attempt、事件、Outbox 一起回滚 |
| folded-tree | 15 | inclusive/self、递归函数聚合与坏输入 |
| heap-dominators | 16 | 可达性、共享引用、直接支配和 retained size |
| sse-frames | 17 | 分块 UTF-8、空行分帧与大整数游标 |
| contract-evolution | 18 | 独立教学 V1/V2 及错误版本、单位、来源 |
| tool-gate | 19 | 阶段、schema、授权和精确事实拒绝路径 |
| checkpoint-replay | 20 | 回执复用、pending 歧义、继承预算和绑定检查 |
| controlled-comparison | 21 | 固定合成配对比较、sham 和独立功能红绿检查 |
| evidence-gate | 22 | 证据来源、顺序、篡改与验收范围 |

CLI 只有断言全通过才返回退出码 0。`--json` 给出范围、每项观察和检查清单。失败应先保存现象，再查对应模块和测试，不通过改变预期把失败洗成成功。

生命周期实验用临时 SQLite 的真实事务，但没有 PostgreSQL 锁、真实 RPC 或多工作进程。其余模型使用内存或固定字节；性能比较的数字明确是合成数据，没有实际 benchmark。网关实验不调用外部模型。`LESSON_SELF_CPU_V2` 只存在于教学验证器，不在正式任务目录注册。实验通过不能表述为生产并发、真实模型诊断或真实业务优化已经验证。

## 复查小项目的真实链路

先按 mini README 编译采集器，并在 `tutorials/mini-drop/control` 下构建 Go 程序。然后回到仓库根目录，给脚本四个实际绝对路径：

```powershell
python tutorials/mini-drop/smoke.py --control (Resolve-Path .local/tutorial-mini/control.exe).Path --collector (Resolve-Path .local/tutorial-mini/collector.exe).Path --python (Get-Command python).Source --analyzer (Resolve-Path tutorials/mini-drop/analyzer.py).Path --output .local/tutorial-mini/smoke.json
```

如果按 mini README 将二进制放到了 `tutorials/mini-drop/.local`，相应调整两个路径。脚本自建临时 workdir、选择空闲回环端口、启动并关闭自己创建的 Go 子进程，运行真实采集与分析，并检查两轮证据、幂等、拒绝输入和重启。输出文件是本次运行记录，不是预置成功样例。它不会连接正式 API、生产数据库或任意 PID。

浏览器工作台在 `/book/workbench`。先按 README 启动 4330 API，再打开前端；Vite 的 `/mini-api` 代理只服务这条本机教学链。进入页面不发请求，点击“连接本地服务”后才读取任务。纯阅读所有章节均无需启动它。

## 完成标准

保存自己的命令、平台、源码版本、真实输出摘要、一个明确失败的反例和恢复后的结果。对每份数据说明它来自真实受控采集、固定合成实验还是正式环境。第 22 章给出完整作业；将代码、证据和局限一起交给另一个人复查，才算完成。
