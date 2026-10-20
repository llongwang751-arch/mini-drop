/**
 * 交互使用指南：说明采集、分析、诊断、验证各环节留下的证据与结论边界。
 * 页面只切换教学内容，不自动发起采集；写入口按会话角色显示，按钮点击再进入相应工作台。
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Check,
  Code,
  Cpu,
  Fingerprint,
  GitDiff,
  HardDrives,
  Info,
  Plus,
  Stack,
  Waveform,
} from "@phosphor-icons/react";
import { useCanWrite } from "../auth-context";
import "../styles/guide.css";

const steps = [
  {
    name: "采集",
    hint: "固定目标，记录现场",
    icon: Waveform,
    title: "从真实信号开始。",
    description:
      "选择已授权的进程或容器，再按问题选择采样方式。采集节点只开放自检通过的能力；已有文件也可以直接导入。",
    points: [
      "确认目标、采样时长与资源预算",
      "保留任务时间线和原始制品",
      "采集状态与分析状态分别记录",
    ],
    path: ["授权目标", "受控采样", "原始制品"],
    note: "不同工具依赖不同运行时与内核权限，先在节点页查看可用能力。",
  },
  {
    name: "分析",
    hint: "把采样变成可读证据",
    icon: Stack,
    title: "先读证据，再做判断。",
    description:
      "分析器将原始文件转换为对应类型的报告。CPU 看调用栈，堆内存看对象与引用路径，I/O 看实际观测统计。",
    points: [
      "先核对样本数量与采集质量",
      "结合调用链区分自身开销与总计开销",
      "沿任务记录和制品校验信息追溯来源",
    ],
    path: ["原始制品", "类型化分析", "结果与调用链"],
    note: "火焰图宽度表示采样权重，不能直接当作请求延迟。",
  },
  {
    name: "诊断",
    hint: "用工具检验排查假设",
    icon: Fingerprint,
    title: "让问题带着证据前进。",
    description:
      "用自然语言描述异常，审阅采集计划后再执行。AgentScope 驱动的 AI 诊断会调用受限工具检查热点、质量和调用链，并提出可复测的假设。",
    points: [
      "计划根据节点能力与目标权限生成",
      "工具调用与引用证据可在调查中查看",
      "信息不足时保留不确定性，补充采样",
    ],
    path: ["描述异常", "审阅计划", "证据与假设"],
    note: "AI 解释需要结合证据判断，引用可校验不等于结论一定正确。",
  },
  {
    name: "验证",
    hint: "复测、对照，再次诊断",
    icon: GitDiff,
    title: "把判断交给下一次验证。",
    description:
      "确认复测计划后，系统重新采集、比较结果并再次诊断。已绑定源码的项目还可定位到文件与行号，在隔离副本中验证补丁。",
    points: [
      "对齐目标、版本与采样条件",
      "对照基线、复测结果与新的诊断",
      "源码补丁通过功能回归和性能对照再评估",
    ],
    path: ["基线证据", "复测与对照", "再次诊断"],
    note: "复现异常与确认根因是不同结论；源码修复依赖已登记项目，不会直接发布原业务代码。",
  },
];
const scenarios = [
  {
    icon: Cpu,
    title: "CPU 高，响应变慢",
    tools: "CPU 采样",
    detail: "从进程与运行时开始",
    read: "热点函数、调用链、自身与总计开销",
  },
  {
    icon: Stack,
    title: "内存增长，难以释放",
    tools: "堆内存 / 分配追踪",
    detail: "区分存活对象与分配行为",
    read: "支配对象、引用路径、分配栈",
  },
  {
    icon: HardDrives,
    title: "I/O 等待，资源异常",
    tools: "I/O / 资源快照",
    detail: "先确认目标与内核能力",
    read: "实际观测量、等待与资源使用",
  },
  {
    icon: Code,
    title: "需要验证源码或二进制优化",
    tools: "源码对照 / BOLT",
    detail: "使用已绑定项目或受控 ELF",
    read: "输出一致性、回归与性能对照",
  },
];
const questions = [
  {
    title: "火焰图的宽度，就是函数耗时吗？",
    answer:
      "宽度表示当前采样度量的权重，纵向表示调用层级。自身开销不包含子调用，总计开销包含子调用。先核对采样类型、样本量和质量，再结合调用链判断；不要把采样比例直接换算成业务请求的 P95 或 P99。",
  },
  {
    title: "复测成功，就证明找到了根因吗？",
    answer:
      "再次观察到相似热点，说明问题可能可复现。要支持根因判断，还需要对齐环境与版本、控制变量，并观察针对性修改后的对照结果。证据不足或只完成重复采样时，应保留相应边界。",
  },
  {
    title: "AI 可以定位源码并自动修改吗？",
    answer:
      "对已经登记源码、版本和测试入口的项目，可以核对文件 SHA、定位行号，并生成最小补丁。目前已接入 Python CRM 与 Saber 的受控组件。补丁在无网络的隔离副本中运行功能回归和性能对照，原业务仓库不会被直接修改或自动发布。",
  },
  {
    title: "为什么有些节点看不到某种采集方式？",
    answer:
      "可用能力由节点实际工具、运行时、内核和授权范围共同决定。采集端 Agent 会自检并校验目标身份；前端不会把未满足条件的工具当作可用能力。接入新节点需要配置专用证书、控制面地址、工作目录与采集预算，先在节点页核对状态。",
  },
];

export default function Guide() {
  const canWrite = useCanWrite();
  const [active, setActive] = useState(0);
  return (
    <div className="guide-page">
      <header className="guide-hero">
        <div className="guide-hero-copy">
          <p className="guide-kicker">使用指南 / FIELD GUIDE</p>
          <h1>
            从采集到洞察<span>让优化有据可循。</span>
          </h1>
          <p className="guide-lead">
            从一次采样开始，沿证据找到问题，再用复测检验判断。
          </p>
          <a className="guide-text-link" href="#guide-workflow">
            了解诊断流程
            <ArrowDown size={17} aria-hidden="true" />
          </a>
        </div>
        <figure
          className="guide-loop"
          aria-label="采集、分析、诊断、验证构成诊断闭环"
        >
          <div className="guide-loop-drawing" aria-hidden="true">
            <div className="guide-loop-orbit" />
            <div className="guide-loop-core">
              <Fingerprint size={38} weight="light" />
              <span>证据</span>
              <small>EVIDENCE</small>
            </div>
            {steps.map(({ name, icon: Icon }, i) => (
              <div
                className={`guide-loop-node guide-loop-node-${i}`}
                key={name}
              >
                <Icon size={22} weight="light" />
                <span>{name}</span>
              </div>
            ))}
            <ArrowRight
              className="guide-loop-arrow guide-loop-arrow-top"
              size={16}
            />
            <ArrowRight
              className="guide-loop-arrow guide-loop-arrow-bottom"
              size={16}
            />
          </div>
          <figcaption>从信号出发，以验证收尾。</figcaption>
        </figure>
      </header>
      <nav className="guide-index" aria-label="本页导航">
        {[
          ["诊断流程", "guide-workflow"],
          ["按现象选择", "guide-scenarios"],
          ["结果与边界", "guide-reading"],
          ["开始使用", "guide-start"],
        ].map(([label, id], i) => (
          <a href={`#${id}`} key={id}>
            <span aria-hidden="true">0{i + 1}</span>
            {label}
            <ArrowDown size={14} aria-hidden="true" />
          </a>
        ))}
      </nav>
      <section
        id="guide-workflow"
        className="guide-section guide-workflow"
        aria-labelledby="guide-workflow-title"
      >
        <div className="guide-section-heading">
          <span className="guide-section-number" aria-hidden="true">
            01
          </span>
          <div>
            <h2 id="guide-workflow-title">一次排查，四个关键环节。</h2>
            <p>
              选择一个环节，了解接下来做什么、留下什么，以及能得出什么结论。
            </p>
          </div>
        </div>
        <div className="guide-workflow-layout">
          <div className="guide-step-list" aria-label="诊断流程环节">
            {steps.map(({ name, hint, icon: Icon }, i) => (
              <button
                id={`guide-step-${i}`}
                className="guide-step"
                key={name}
                aria-pressed={active === i}
                aria-controls="guide-step-panel"
                onClick={() => setActive(i)}
              >
                <span className="guide-step-icon" aria-hidden="true">
                  <Icon size={23} />
                </span>
                <span className="guide-step-copy">
                  <strong>{name}</strong>
                  <span aria-hidden="true">{hint}</span>
                </span>
                <ArrowUpRight size={18} aria-hidden="true" />
              </button>
            ))}
          </div>
          <div
            id="guide-step-panel"
            className="guide-lesson"
            role="region"
            aria-labelledby={`guide-step-${active}`}
          >
            {steps.map((step, index) => {
              const StepIcon = step.icon;
              return (
                <div
                  className="guide-lesson-pane"
                  data-active={active === index}
                  aria-hidden={active !== index}
                  inert={active !== index}
                  key={step.name}
                >
                  <div className="guide-lesson-content">
                    <div className="guide-lesson-meta" aria-hidden="true">
                      <StepIcon size={25} weight="light" />
                      <span>
                        0{index + 1}
                        <span> / 04</span>
                      </span>
                    </div>
                    <h3>{step.title}</h3>
                    <p className="guide-lesson-description">
                      {step.description}
                    </p>
                    <ul className="guide-checklist">
                      {step.points.map((point) => (
                        <li key={point}>
                          <Check size={15} aria-hidden="true" />
                          {point}
                        </li>
                      ))}
                    </ul>
                    <ol
                      className="guide-path"
                      aria-label={`${step.name}的证据路径`}
                    >
                      {step.path.map((label, i) => (
                        <li key={label}>
                          {i > 0 && <ArrowRight size={13} aria-hidden="true" />}
                          <span>{label}</span>
                        </li>
                      ))}
                    </ol>
                  </div>
                  <div className="guide-lesson-note">
                    <Info size={18} aria-hidden="true" />
                    <p>{step.note}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </section>
      <section
        id="guide-scenarios"
        className="guide-section"
        aria-labelledby="guide-scenarios-title"
      >
        <div className="guide-section-heading">
          <span className="guide-section-number" aria-hidden="true">
            02
          </span>
          <div>
            <h2 id="guide-scenarios-title">从你看到的现象开始。</h2>
            <p>先选信号，再看证据。节点实际开放的能力决定可用的采集方式。</p>
          </div>
        </div>
        <div className="guide-scenarios">
          <div className="guide-scenario-labels" aria-hidden="true">
            <span>观察到的现象</span>
            <span>排查入口</span>
            <span>重点阅读</span>
          </div>
          {scenarios.map(({ icon: Icon, title, tools, detail, read }) => (
            <article className="guide-scenario" key={title}>
              <h3>
                <Icon size={21} weight="light" aria-hidden="true" />
                {title}
              </h3>
              <div>
                <strong>{tools}</strong>
                <p>{detail}</p>
              </div>
              <p className="guide-scenario-reading">{read}</p>
            </article>
          ))}
        </div>
      </section>
      <section
        id="guide-reading"
        className="guide-section guide-reading"
        aria-labelledby="guide-reading-title"
      >
        <div className="guide-reading-intro">
          <span className="guide-section-number" aria-hidden="true">
            03
          </span>
          <h2 id="guide-reading-title">
            读懂结果，
            <br />
            也知道边界。
          </h2>
          <p>一份有用的报告，既解释观察到了什么，也说明还没有证明什么。</p>
          <Fingerprint size={40} weight="thin" aria-hidden="true" />
        </div>
        <div className="guide-questions">
          {questions.map(({ title, answer }) => (
            <details key={title}>
              <summary>
                {title}
                <Plus size={19} aria-hidden="true" />
              </summary>
              <p>{answer}</p>
            </details>
          ))}
        </div>
      </section>
      <section
        id="guide-start"
        className="guide-start"
        aria-labelledby="guide-start-title"
      >
        <div>
          <p className="guide-start-label">下一步</p>
          <h2 id="guide-start-title">带着一个问题，开始排查。</h2>
          <p>
            {canWrite
              ? "描述异常，让智能调查帮助你组织下一次采集。"
              : "从节点能力和已有诊断记录开始了解现场。"}
          </p>
        </div>
        <div className="guide-start-actions">
          {canWrite && (
            <Link className="button primary" to="/investigations">
              发起智能调查
              <ArrowUpRight size={17} aria-hidden="true" />
            </Link>
          )}
          <div className="guide-secondary-actions">
            {canWrite && (
              <Link className="guide-text-link" to="/tasks/new">
                创建诊断
                <ArrowUpRight size={15} aria-hidden="true" />
              </Link>
            )}
            <Link className="guide-text-link" to="/agents">
              查看节点
              <ArrowUpRight size={15} aria-hidden="true" />
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
