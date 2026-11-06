import type { BookChapter } from "./types";

/** Practical lessons are separate chunks: opening one lesson loads only its text. */
export const practiceChapters: BookChapter[] = [
  {
    id: 11,
    title: "从空目录接起四种语言",
    subtitle: "写出、运行并核对第一条 C++ → Go → Python → React 链路。",
    part: "第三部分 · 亲手实现采集与分析",
    readingMinutes: 65,
    lead: "前十章读现有代码。从这里开始，你会运行一份独立的小实现，逐个检查进程、文件和 HTTP 交接，最后在页面看到来自本次运行的结果。",
    prerequisites:
      "先读第 1、2 章。准备本地 C++ 编译器、Go、Python 与现有前端依赖。",
    loadContent: () =>
      import("./content/chapter11.md?raw").then((m) => m.default),
    sources: [
      {
        path: "tutorials/mini-drop/README.md",
        label: "四语言参考实现的启动与边界",
        local: true,
      },
      {
        path: "web/src/book/LabWorkbench.tsx",
        label: "本地实战工作台",
        local: true,
      },
    ],
  },
  {
    id: 12,
    title: "让 C++ 交出一份可信的数据",
    subtitle: "实现受控采集，逐个处理参数、CPU 单位、进程身份和退出。",
    part: "第三部分 · 亲手实现采集与分析",
    readingMinutes: 65,
    lead: "采集器退出了，不代表它采到了你想看的东西。先把本进程 CPU 计账做对，再沿着正式 Runner 的代码理解目标身份、权限和停止条件。",
    prerequisites: "完成第 11 章，并能找到这次任务生成的 raw.json。",
    loadContent: () =>
      import("./content/chapter12.md?raw").then((m) => m.default),
    sources: [
      {
        path: "tutorials/mini-drop/collector.cpp",
        label: "受控进程 CPU 采集器",
        local: true,
      },
      { path: "core/src/runner.cpp", label: "正式 Runner 的受控执行" },
    ],
  },
  {
    id: 13,
    title: "把 Go API 一步步写出来",
    subtitle: "输入校验、幂等键、状态事件和文件 journal 的真实写入顺序。",
    part: "第三部分 · 亲手实现采集与分析",
    readingMinutes: 70,
    lead: "收到请求只是开始。你要让重发得到同一项工作，让磁盘写入失败时内存也不撒谎，再弄清为什么文件实现还不能承担正式数据库的责任。",
    prerequisites: "完成第 11 章；知道 JSON、HTTP 状态码和 Go 基本结构。",
    loadContent: () =>
      import("./content/chapter13.md?raw").then((m) => m.default),
    sources: [
      {
        path: "tutorials/mini-drop/control/server.go",
        label: "严格的本地 HTTP 入口",
        local: true,
      },
      {
        path: "tutorials/mini-drop/control/store.go",
        label: "状态、事件与幂等键一并写入",
        local: true,
      },
    ],
  },
  {
    id: 14,
    title: "让任务在失败以后站得住",
    subtitle: "亲手重现消息重发、旧轮次回写、租约接管和事务回滚。",
    part: "第三部分 · 亲手实现采集与分析",
    readingMinutes: 75,
    lead: "把“正常时能跑”改成“失败时说得清”。先观察小实现怎样恢复，再用可控故障把生产工程里的 Outbox、attempt 和 lease 条件拆开验证。",
    prerequisites: "完成第 13 章；读过第 4、6 章的状态与恢复部分。",
    loadContent: () =>
      import("./content/chapter14.md?raw").then((m) => m.default),
    sources: [
      {
        path: "tutorials/labs/lifecycle.py",
        label: "四个生命周期故障实验",
        local: true,
      },
      {
        path: "tutorials/mini-drop/control/server_test.go",
        label: "小实现的恢复与隔离检查",
        local: true,
      },
    ],
  },
  {
    id: 15,
    title: "亲手把采样文本算成调用树",
    subtitle: "实现 folded parser，验证 inclusive、self、递归与输入限额。",
    part: "第三部分 · 亲手实现采集与分析",
    readingMinutes: 70,
    lead: "一棵看着像火焰图的树，也可能每个数字都算错。这一章从几行明确标注的合成输入开始，一步步推导节点值，并故意喂入坏数据。",
    prerequisites: "会写 Python 循环与字典；先读第 6 章。",
    loadContent: () =>
      import("./content/chapter15.md?raw").then((m) => m.default),
    sources: [
      {
        path: "tutorials/labs/analysis.py",
        label: "有界 parser 与调用树实验",
        local: true,
      },
      {
        path: "tutorials/labs/test_analysis.py",
        label: "递归和错误输入检查",
        local: true,
      },
    ],
  },
  {
    id: 16,
    title: "从对象关系推到 retained size",
    subtitle: "画清共享引用，再写一个能手算核对的支配关系算法。",
    part: "第三部分 · 亲手实现采集与分析",
    readingMinutes: 75,
    lead: "删掉一个对象，到底能释放多少内存？我们从几条引用边推起，保留共享对象和不可达对象这些容易被漂亮图表掩盖的区别。",
    prerequisites: "完成第 15 章；知道集合交集和有向图的基本含义。",
    loadContent: () =>
      import("./content/chapter16.md?raw").then((m) => m.default),
    sources: [
      {
        path: "tutorials/labs/analysis.py",
        label: "教学支配集合算法与图检查",
        local: true,
      },
      { path: "analyzer/drop_analyzer/worker.py", label: "正式分析任务入口" },
    ],
  },
  {
    id: 17,
    title: "把任务与证据接到 React 页面",
    subtitle: "亲手连接本地 API，再拆开 SSE 分帧和异步结果串页问题。",
    part: "第三部分 · 亲手实现采集与分析",
    readingMinutes: 65,
    lead: "页面上每个数字都该找得到来源。先用工作台读取实际任务，再用故意切碎的合成字节流学习 SSE，分清网络读取、状态与渲染各自的工作。",
    prerequisites: "完成第 11 章；会 React 基本 state、effect 和 fetch。",
    loadContent: () =>
      import("./content/chapter17.md?raw").then((m) => m.default),
    sources: [
      {
        path: "web/src/book/LabWorkbench.tsx",
        label: "显式连接、轮询与证据展示",
        local: true,
      },
      {
        path: "tutorials/labs/analysis.py",
        label: "独立 SSE 字节流实验",
        local: true,
      },
    ],
  },
  {
    id: 18,
    title: "练一次跨语言契约演进",
    subtitle: "从教学任务提案到版本、单位和兼容性检查，列清每一端的责任。",
    part: "第三部分 · 亲手实现采集与分析",
    readingMinutes: 65,
    lead: "多加一个任务类型，会同时影响谁？用独立的教学 V2 提案走一遍字段设计、拒绝路径和迁移检查，再对照正式契约；练习不会把提案注册成生产能力。",
    prerequisites: "完成第 12、13、15 章；读过第 3 章的契约部分。",
    loadContent: () =>
      import("./content/chapter18.md?raw").then((m) => m.default),
    sources: [
      {
        path: "tutorials/labs/analysis.py",
        label: "教学契约版本与来源校验",
        local: true,
      },
      { path: "contracts/task-kinds.json", label: "正式任务类型目录" },
      { path: "contracts/drop.proto", label: "跨服务协议字段编号" },
    ],
  },
  {
    id: 19,
    title: "从零写一个工具网关",
    subtitle: "把模型的工具请求变成受约束的候选动作，实测拒绝路径。",
    part: "第四部分 · 诊断、回放与毕业实战",
    readingMinutes: 70,
    lead: "模型可以提出请求，执行器需要自己核对阶段、权限、参数和事实。我们写一份不调用外部模型的网关，靠固定输入看清每一道检查实际阻止了什么。",
    prerequisites: "读过第 8 章；会 Python 函数、枚举与基本输入校验。",
    loadContent: () =>
      import("./content/chapter19.md?raw").then((m) => m.default),
    sources: [
      {
        path: "tutorials/labs/diagnosis.py",
        label: "固定候选工具请求的实际校验",
        local: true,
      },
      {
        path: "agent_runtime/drop_agent_runtime/runner.py",
        label: "正式诊断运行循环",
      },
    ],
  },
  {
    id: 20,
    title: "给诊断加上 checkpoint 和回放",
    subtitle: "区分已完成、待确认与可重放，算清模型和工具预算。",
    part: "第四部分 · 诊断、回放与毕业实战",
    readingMinutes: 75,
    lead: "重启后再跑一次，看似省事，却可能重复执行已有副作用。先把动作收据和歧义状态记录下来，再验证修改预算或重放顺序为什么必须被拒绝。",
    prerequisites: "完成第 19 章；理解第 14 章的轮次与租约。",
    loadContent: () =>
      import("./content/chapter20.md?raw").then((m) => m.default),
    sources: [
      {
        path: "tutorials/labs/diagnosis.py",
        label: "持久化回放数据与共享预算实验",
        local: true,
      },
      {
        path: "tutorials/labs/test_diagnosis.py",
        label: "篡改、歧义状态与证据检查",
        local: true,
      },
    ],
  },
  {
    id: 21,
    title: "证明补丁有没有改善问题",
    subtitle: "先保住功能，再控制变量、比较候选与 sham，并限制结论。",
    part: "第四部分 · 诊断、回放与毕业实战",
    readingMinutes: 70,
    lead: "跑得更快只是一个观测。功能退步、环境漂移和测量噪声都可能改变解释。这一章把补丁、红绿测试、配对比较和反例放到同一份记录里。",
    prerequisites: "完成第 19、20 章；读过第 9 章。",
    loadContent: () =>
      import("./content/chapter21.md?raw").then((m) => m.default),
    sources: [
      {
        path: "tutorials/labs/diagnosis.py",
        label: "固定合成比较与功能红绿检查",
        local: true,
      },
      { path: "docs/CONTRACT.md", label: "正式结果、验收与证据契约" },
    ],
  },
  {
    id: 22,
    title: "独立交付一次完整实战",
    subtitle: "从启动与失败排查，到验收清单、证据文件和口头讲解。",
    part: "第四部分 · 诊断、回放与毕业实战",
    readingMinutes: 80,
    lead: "收起前面的答案，自己完成一次任务、一次故障恢复和一次证据交付。遇到报错时按现象定位，让最终记录能被另一个人复查。",
    prerequisites: "完成第 11–21 章；保留你自己的命令与运行产物。",
    loadContent: () =>
      import("./content/chapter22.md?raw").then((m) => m.default),
    sources: [
      {
        path: "tutorials/README.md",
        label: "可运行材料与验收入口",
        local: true,
      },
      {
        path: "tutorials/mini-drop/smoke.py",
        label: "四语言链路的独立复查脚本",
        local: true,
      },
      {
        path: "docs/PROJECT_TUTORIAL.md",
        label: "教程范围和验证记录",
        local: true,
      },
    ],
  },
];
