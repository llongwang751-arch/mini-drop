import type { BookChapter } from "./types";
import chapter6 from "./content/chapter6.md?raw";
import chapter7 from "./content/chapter7.md?raw";
import chapter8 from "./content/chapter8.md?raw";
import chapter9 from "./content/chapter9.md?raw";
import chapter10 from "./content/chapter10.md?raw";

export const advancedChapters: BookChapter[] = [
  {
    id: 6,
    title: "Analyzer：把文件变成可信的结果",
    subtitle: "会解析只是开始，还要知道这份结果能不能发布",
    part: "第二部分 · 看懂可靠执行",
    readingMinutes: 45,
    lead: "原始文件传上来了，页面为什么还不能马上画图？这一章接住 C++ Agent 交来的制品，看看 Python 怎样核验、隔离解析，并把结果交还给控制面。",
    prerequisites:
      "已经理解任务与 attempt、短期上传地址，以及采集成功和分析成功的区别。",
    content: chapter6,
    sources: [
      {
        path: "analyzer/drop_analyzer/worker.py",
        label: "下载、LeaseGuard 与处理主线",
      },
      {
        path: "analyzer/drop_analyzer/repository.py",
        label: "领取、续租与发布事务",
      },
      {
        path: "analyzer/drop_analyzer/isolate.py",
        label: "子进程环境与资源边界",
      },
      {
        path: "analyzer/drop_analyzer/result.py",
        label: "来源、单位和解释边界",
      },
      {
        path: "analyzer/tests/test_worker.py",
        label: "坏输入与过期 owner 回归",
      },
      {
        path: "analyzer/tests/test_result.py",
        label: "导入和 PROC_CPU 结果回归",
      },
    ],
  },
  {
    id: 7,
    title: "React：让证据在页面上说清楚",
    subtitle: "实时更新、断线恢复和火焰图，都有自己的正确性问题",
    part: "第二部分 · 看懂可靠执行",
    readingMinutes: 40,
    lead: "后端把状态和制品交出来以后，前端要做的不只是画一个漂亮图表。它还要处理断线、旧请求和大文件，并让读者分清占比、耗时与未知信息。",
    prerequisites:
      "理解任务的采集/分析双状态，知道分析结果来自已登记制品。会读基本的 React effect 和 Promise 即可。",
    content: chapter7,
    sources: [
      {
        path: "web/src/views/TaskDetail.tsx",
        label: "任务详情与页面生命周期",
      },
      {
        path: "web/src/event-stream.ts",
        label: "SSE 解码、游标与恢复",
      },
      {
        path: "web/src/query-policy.ts",
        label: "查询重试与退避",
      },
      {
        path: "web/src/profile-loader.ts",
        label: "Worker 载入与终止",
      },
      {
        path: "web/src/profile-data.ts",
        label: "体积、地址和结果校验",
      },
      {
        path: "web/src/components/flame-analysis.ts",
        label: "权重守恒与显示变换",
      },
    ],
  },
  {
    id: 8,
    title: "诊断 Agent：让模型沿着证据做事",
    subtitle: "先分清两个 Agent，再理解工具循环与控制面的边界",
    part: "第三部分 · 从看见现象到验证判断",
    readingMinutes: 40,
    lead: "Drop 里既有 C++ Agent，也有 AgentScope 驱动的诊断 Agent。它们同名，职责却差得很远。本章用一次调查串起计划、采集、观测与报告验收。",
    prerequisites:
      "读完采集、分析和页面链路；知道规则发现只是相关信号，尚未形成因果结论。",
    content: chapter8,
    sources: [
      {
        path: "agent_runtime/drop_agent_runtime/runner.py",
        label: "原生循环、阶段工具与预算",
      },
      {
        path: "api/internal/service/diagnosis_agentscope.go",
        label: "工具回调和报告验收",
      },
      {
        path: "api/internal/service/agent_runtime.go",
        label: "Go 与 runtime 的私有网关",
      },
      {
        path: "api/internal/service/agent_checkpoint.go",
        label: "检查点绑定与恢复",
      },
      {
        path: "docs/AGENTSCOPE_INTEGRATION.md",
        label: "分工与工程边界",
      },
      {
        path: "contracts/agent-runtime-v1.json",
        label: "运行协议",
      },
      {
        path: "api/internal/service/diagnosis_tools.go",
        label: "本章对照：diagnosis_tools.go",
      },
      {
        path: "docs/JD_QUALITY_DEPTH.md",
        label: "本章对照：JD_QUALITY_DEPTH.md",
      },
      {
        path: "agent_runtime/README.md",
        label: "本章对照：README.md",
      },
    ],
  },
  {
    id: 9,
    title: "复测和修复：怎样证明真的变好了",
    subtitle: "再采一次、补丁通过、业务改善，是三种不同结论",
    part: "第三部分 · 从看见现象到验证判断",
    readingMinutes: 40,
    lead: "一个热点变小了，并不自动意味着优化成功。本章从最容易误读的百分比出发，拆开复测比较、源码修复沙箱和受控因果验证。",
    prerequisites:
      "理解 self/total、采集来源与单位，知道 Agent 的判断要引用实际工具观测。",
    content: chapter9,
    sources: [
      {
        path: "api/internal/service/diagnosis_verify.go",
        label: "复测状态与比较口径",
      },
      {
        path: "api/internal/service/code_repair.go",
        label: "源码准入、租约与验收",
      },
      {
        path: "api/internal/service/code_executor.go",
        label: "类型化执行器客户端",
      },
      {
        path: "ops/code_executor.py",
        label: "隔离测试和测量执行",
      },
      {
        path: "api/internal/service/code_repair_test.go",
        label: "协议、行号与权限回归",
      },
      {
        path: "docs/DIAGNOSIS_AGENT_V2.md",
        label: "复测与源码闭环说明",
      },
      {
        path: "ops/test_code_executor.py",
        label: "本章对照：test_code_executor.py",
      },
      {
        path: "docs/CAPABILITY_GAPS.md",
        label: "本章对照：CAPABILITY_GAPS.md",
      },
    ],
  },
  {
    id: 10,
    title: "从读懂到改对：完成你的第一个改动",
    subtitle: "用测试和证据说明做到了什么，也说明还没做到什么",
    part: "第四部分 · 带着证据继续开发",
    readingMinutes: 45,
    lead: "学完架构不等于能安全修改系统。最后一章把契约、回归、恢复和证据串成一套开发方法，并给出一个无需碰生产的毕业练习。",
    prerequisites:
      "能讲清任务全链路、当前 attempt 与租约围栏、分析单位，以及模型结论的验证边界。",
    content: chapter10,
    sources: [
      {
        path: "docs/CONTRACT.md",
        label: "共享状态、接口与授权契约",
      },
      {
        path: "docs/LOCAL_DEVELOPMENT.md",
        label: "独立本地环境",
      },
      {
        path: "scripts/quality/system/README.md",
        label: "完整链路与恢复门禁",
      },
      {
        path: "scripts/quality/protocol/README.md",
        label: "协议与未知输入检查",
      },
      {
        path: "scripts/quality/reports/README.md",
        label: "不可变质量记录",
      },
      {
        path: "docs/JD_QUALITY_DEPTH.md",
        label: "实际证据与当前局限",
      },
      {
        path: "docs/DELIVERY_MATRIX.md",
        label: "交付范围与证据矩阵",
      },
      {
        path: "api/internal/service/diagnosis_verify.go",
        label: "本章对照：diagnosis_verify.go",
      },
      {
        path: "api/internal/testutil/postgres.go",
        label: "本章对照：postgres.go",
      },
      {
        path: "scripts/quality/system/lab.py",
        label: "本章对照：lab.py",
      },
      {
        path: "contracts/quality-run.schema.json",
        label: "本章对照：quality-run.schema.json",
      },
    ],
  },
];
