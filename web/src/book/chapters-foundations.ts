import type { BookChapter } from "./types";
import chapter1 from "./content/chapter1.md?raw";
import chapter2 from "./content/chapter2.md?raw";
import chapter3 from "./content/chapter3.md?raw";
import chapter4 from "./content/chapter4.md?raw";
import chapter5 from "./content/chapter5.md?raw";

export const foundationChapters: BookChapter[] = [
  {
    id: 1,
    title: "先看懂 Drop 在忙什么",
    subtitle: "从一个变慢的 Python 接口，走完采集、分析和诊断这条路。",
    part: "第一部分 · 跑通一条任务",
    readingMinutes: 40,
    lead: "用户说“接口又慢了”，CPU 曲线也确实升了。然后呢？这一章先把问题摆在桌上，再看看 Drop 怎样把一句抱怨变成可以核对的证据。",
    prerequisites:
      "会写一个简单的程序，知道 HTTP 请求和进程是什么即可。暂时不需要读懂 C++、Go 和 Python 的全部语法。",
    content: chapter1,
    sources: [
      {
        path: "docs/CODE_READING_GUIDE.md",
        label: "现有源码阅读路线与两个 Agent 的职责",
      },
      {
        path: "contracts/task-kinds.json",
        label: "Python CPU 输入、Runner 与分析管线约定",
      },
      {
        path: "db/migrations/001_initial.sql",
        label: "任务、执行和双状态的数据库约束",
      },
      {
        path: "core/src/agent.cpp",
        label: "C++ 采集 Agent 主循环与恢复",
      },
      {
        path: "analyzer/drop_analyzer/worker.py",
        label: "本章对照：worker.py",
      },
      {
        path: "agent_runtime/drop_agent_runtime/runner.py",
        label: "本章对照：runner.py",
      },
    ],
  },
  {
    id: 2,
    title: "先跑通，再往里面看",
    subtitle: "用独立的本地栈完成一次真实任务，知道每个命令在等什么。",
    part: "第一部分 · 跑通一条任务",
    readingMinutes: 40,
    lead: "“代码下载好了”和“系统能工作”之间，往往隔着 Docker、证书、数据库和代理。这里把第一轮启动拆开，让每一步都有可以观察的结果。",
    prerequisites:
      "完成第 1 章；本机具备 Python 3.11+，并能使用 Docker Desktop 的 Linux 容器或 Linux Docker Engine。",
    content: chapter2,
    sources: [
      {
        path: "docs/LOCAL_DEVELOPMENT.md",
        label: "本地环境、工具前提和实际验证边界",
      },
      {
        path: "scripts/dev.py",
        label: "doctor、隔离配置、启动与真实 smoke 实现",
      },
      {
        path: "scripts/dev.ps1",
        label: "Windows 本地入口",
      },
      {
        path: "deploy/minio/README.md",
        label: "固定 MinIO 依赖与构建来源",
      },
      {
        path: "web/package.json",
        label: "前端本地开发命令",
      },
    ],
  },
  {
    id: 3,
    title: "四种语言，怎样说同一种话",
    subtitle: "读懂任务契约，先分清“要采什么”“能采什么”和“已经发生什么”。",
    part: "第一部分 · 跑通一条任务",
    readingMinutes: 40,
    lead: "前端有一个 Python CPU 选项，并不意味着后台就知道怎么执行。跨语言系统能接起来，靠的是每一层都遵守的输入、状态和文件约定。",
    prerequisites:
      "完成本地启动或读过第 2 章；能看懂 JSON，知道函数参数和返回值的含义。",
    content: chapter3,
    sources: [
      {
        path: "contracts/task-kinds.json",
        label: "类型目录、参数 Schema 和文件格式真源",
      },
      {
        path: "contracts/drop.proto",
        label: "gRPC 消息、字段编号与上传授权",
      },
      {
        path: "db/migrations/001_initial.sql",
        label: "跨服务持久状态与表约束",
      },
      {
        path: "api/internal/domain/task.go",
        label: "请求归一化与纯领域校验",
      },
      {
        path: "docs/CONTRACT.md",
        label: "REST、任务状态和兼容性契约",
      },
      {
        path: "api/internal/domain/kinds.go",
        label: "本章对照：kinds.go",
      },
      {
        path: "api/internal/service/tasks.go",
        label: "本章对照：tasks.go",
      },
      {
        path: "core/src/runner.cpp",
        label: "本章对照：runner.cpp",
      },
      {
        path: "core/src/process.cpp",
        label: "本章对照：process.cpp",
      },
      {
        path: "analyzer/drop_analyzer/worker.py",
        label: "本章对照：worker.py",
      },
      {
        path: "analyzer/drop_analyzer/registry.py",
        label: "本章对照：registry.py",
      },
      {
        path: "web/src/task-schema.ts",
        label: "本章对照：task-schema.ts",
      },
    ],
  },
  {
    id: 4,
    title: "点下创建以后，Go 做了什么",
    subtitle: "把一次 HTTP 请求变成可靠的任务：权限、幂等、事务和 Outbox。",
    part: "第二部分 · 看懂可靠执行",
    readingMinutes: 45,
    lead: "网速一慢，人很容易再点一次。服务一重启，未完成工作也可能被忘掉。这章从这些日常小故障出发，理解控制面为什么不能只是“收到请求，调用采集器”。",
    prerequisites:
      "了解第 3 章的 task、attempt、类型目录；知道数据库事务能让一组写入一起提交或回滚。",
    content: chapter4,
    sources: [
      {
        path: "api/internal/service/tasks.go",
        label: "创建校验、幂等处理、导入与后台派发",
      },
      {
        path: "api/internal/repository/postgres.go",
        label: "事务创建、取消、重试和 Outbox",
      },
      {
        path: "api/internal/repository/fencing.go",
        label: "当前执行与写入条件",
      },
      {
        path: "api/internal/repository/authorization.go",
        label: "资源授权实现",
      },
      {
        path: "docs/CONTRACT.md",
        label: "会话、CSRF、幂等与任务生命周期",
      },
      {
        path: "api/internal/httpapi/server.go",
        label: "本章对照：server.go",
      },
    ],
  },
  {
    id: 5,
    title: "采集 Agent 怎样安全地动手",
    subtitle: "注册能力、固定目标、执行工具，再把完成和确认分开。",
    part: "第二部分 · 看懂可靠执行",
    readingMinutes: 45,
    lead: "收到一个 PID 并不等于找到了正确的人；工具退出也不等于平台收到结果。采集端的大部分工程，就是把这些看似相邻、实际不同的步骤分清楚。",
    prerequisites:
      "知道 task/attempt 与 Outbox；能大致读懂 C++ 的条件判断和数组，不需要先学会全部语言特性。",
    content: chapter5,
    sources: [
      {
        path: "core/src/agent.cpp",
        label: "注册、心跳、执行、上传与恢复",
      },
      {
        path: "core/src/target.cpp",
        label: "Linux 目标身份与本地策略",
      },
      {
        path: "core/src/runner.cpp",
        label: "类型化 Runner 和 py-spy 参数构造",
      },
      {
        path: "core/src/process.cpp",
        label: "argv 执行、预算、超时与取消",
      },
      {
        path: "core/src/helper.cpp",
        label: "权限隔离与 helper 协议",
      },
      {
        path: "core/src/manifest.cpp",
        label: "采集清单与制品身份",
      },
      {
        path: "core/tests/process_test.cpp",
        label: "本章对照：process_test.cpp",
      },
    ],
  },
];
