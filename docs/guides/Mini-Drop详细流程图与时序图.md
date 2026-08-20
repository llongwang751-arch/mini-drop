# Mini-Drop 详细流程图与时序图

> 本文用于理解系统、设计评审和导师汇报。图中区分了“基础性能采集链路”“AI 循证诊断链路”“真实故障评测链路”和“多机诊断链路”。

---

## 1. 项目总体架构图

```mermaid
flowchart LR
    User["用户<br/>开发工程师 / SRE"]

    subgraph Frontend["前端层"]
        Web["React Web<br/>任务面板 / AI诊断 / 历史 / 审计"]
    end

    subgraph Access["服务接入与业务层"]
        API["Go API Server<br/>参数校验 / 任务接口 / 查询接口"]
        Biz["业务 Server<br/>状态管理 / AI会话 / 权限与审计"]
    end

    subgraph ControlLayer["控制层"]
        Control["C++ Control Plane<br/>任务调度 / Agent管理 / 状态上报"]
        Scheduler["调度器<br/>选择目标Agent / 超时 / 重试"]
    end

    subgraph HostA["目标 Linux 主机 A"]
        AgentA["Agent A<br/>心跳 / 领取任务 / 上传产物"]
        AppA["目标服务 A<br/>PID 101"]
        CollectorsA["采集器<br/>perf / eBPF / py-spy<br/>async-profiler / pprof / procfs"]
        AgentA --> CollectorsA
        CollectorsA --> AppA
    end

    subgraph HostB["目标 Linux 主机 B"]
        AgentB["Agent B"]
        AppB["目标服务 B<br/>PID 202"]
        CollectorsB["采集器"]
        AgentB --> CollectorsB
        CollectorsB --> AppB
    end

    subgraph DataLayer["数据层"]
        DB[("PostgreSQL<br/>任务 / 状态 / Agent / 假设 / 证据 / 审计")]
        Object[("MinIO<br/>原始采集文件 / 分析产物 / 快照")]
    end

    subgraph AnalysisLayer["分析与智能层"]
        Analyzer["Python Analyzer<br/>解析原始数据 / 火焰图 / TopN / 趋势"]
        AI["AI诊断编排器<br/>范围确认 / 假设 / 决策树 / 报告"]
        Guard["安全与证据门禁<br/>工具白名单 / 审批 / evidence_refs"]
        Eval["评测引擎<br/>Golden / Campaign / Oracle对比"]
    end

    User --> Web
    Web --> API
    API --> Biz
    Biz --> DB
    Biz --> Control
    Control --> Scheduler
    Scheduler --> AgentA
    Scheduler --> AgentB
    AgentA --> Control
    AgentB --> Control
    AgentA --> Object
    AgentB --> Object
    Object --> Analyzer
    Analyzer --> Object
    Analyzer --> DB
    DB --> AI
    Object --> AI
    AI --> Guard
    Guard --> Control
    AI --> DB
    Eval --> AI
    Eval --> DB
    Biz --> API
    API --> Web
```

### 人话解释

- **Web**：用户填写任务、看进度和结果。
- **Go API / Server**：创建任务、保存信息、向前端提供接口。
- **Control Plane**：找到正确的 Agent 并下发任务。
- **Agent**：驻留在被检查机器上的执行程序，不只是一个脚本。
- **采集器**：Agent 调用的具体工具，例如 `perf`、`py-spy` 和 eBPF。
- **MinIO**：保存体积较大的原始文件和分析文件。
- **PostgreSQL**：保存任务状态、诊断假设、证据引用和审计记录。
- **Analyzer**：把原始数据加工成火焰图、TopN 和趋势图。
- **AI 编排器**：根据问题提出假设，决定下一步查什么，并生成可追溯结论。

---

## 2. 普通性能采集完整流程图

```mermaid
flowchart TD
    A["用户选择 Agent、PID、采集器、时长和频率"]
    B{"输入是否合法"}
    C["Go API 创建任务"]
    D["数据库写入 PENDING 和 reason"]
    E["Control Plane 选择在线 Agent"]
    F{"Agent 是否在线且支持该采集器"}
    G["Agent 领取任务"]
    H["写入 RUNNING 和 reason"]
    I{"目标 PID 是否存在"}
    J["调用 perf / py-spy / eBPF 等采集器"]
    K{"采集是否成功"}
    L["生成原始采集产物"]
    M["写入 UPLOADING 和 reason"]
    N["上传 MinIO并计算 SHA256"]
    O{"产物完整性是否通过"}
    P["写入 ANALYZING 和 reason"]
    Q["Python Analyzer 解析产物"]
    R{"分析是否成功"}
    S["生成火焰图 / TopN / 趋势图"]
    T["写入 DONE 和 reason"]
    U["Web 展示时间线、产物和可视化"]
    X["写入 FAILED 和明确原因"]

    A --> B
    B -- 否 --> X
    B -- 是 --> C
    C --> D --> E --> F
    F -- 否 --> X
    F -- 是 --> G --> H --> I
    I -- 否 --> X
    I -- 是 --> J --> K
    K -- 否 --> X
    K -- 是 --> L --> M --> N --> O
    O -- 否 --> X
    O -- 是 --> P --> Q --> R
    R -- 否 --> X
    R -- 是 --> S --> T --> U
```

### 状态机

```mermaid
stateDiagram-v2
    [*] --> PENDING: API 创建任务
    PENDING --> RUNNING: Agent 领取任务
    RUNNING --> UPLOADING: 采集完成
    UPLOADING --> ANALYZING: 产物上传并校验
    ANALYZING --> DONE: Analyzer 生成结果

    PENDING --> FAILED: 无可用 Agent / 参数错误
    RUNNING --> FAILED: PID 不存在 / 工具失败 / 超时
    UPLOADING --> FAILED: 上传失败 / 哈希不一致
    ANALYZING --> FAILED: 解析失败 / 产物格式错误

    FAILED --> ANALYZING: 原始产物有效时重放分析
    DONE --> [*]
    FAILED --> [*]
```

> 每次状态迁移都需要落库，并记录 `reason`、时间、执行者和对应的 `TaskAttempt`。

---

## 3. 普通采集任务时序图

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户
    participant Web as React Web
    participant API as Go API
    participant DB as PostgreSQL
    participant CP as C++ Control Plane
    participant Agent as Agent
    participant Tool as 采集器
    participant Obj as MinIO
    participant Analyzer as Python Analyzer

    User->>Web: 填写 Agent、PID、采集器、时长
    Web->>API: POST 创建任务
    API->>API: 校验参数与权限
    API->>DB: 创建 Task 和 TaskAttempt
    API->>DB: 写入 PENDING + reason
    API-->>Web: 返回 task_id

    loop 页面实时刷新 / SSE
        Web->>API: 查询任务状态
        API->>DB: 读取最新状态
        API-->>Web: 返回状态与原因
    end

    CP->>DB: 查询待执行任务
    CP->>Agent: 下发任务
    Agent->>DB: 写入 RUNNING + reason
    Agent->>Agent: 检查 PID、权限和工具能力
    Agent->>Tool: 执行真实采集
    Tool-->>Agent: 返回原始数据和退出码

    alt 采集成功
        Agent->>DB: 写入 UPLOADING + reason
        Agent->>Agent: 计算 SHA256
        Agent->>Obj: 上传原始产物
        Obj-->>Agent: 返回对象地址
        Agent->>DB: 登记产物与哈希
        Agent->>DB: 写入 ANALYZING + reason
        Analyzer->>Obj: 下载原始产物
        Analyzer->>Analyzer: 解析、聚合、生成可视化
        Analyzer->>Obj: 上传分析产物
        Analyzer->>DB: 保存 TopN、可视化地址和建议
        Analyzer->>DB: 写入 DONE + reason
        Web->>API: 查询任务详情
        API-->>Web: 返回时间线、产物和可视化
        Web-->>User: 展示火焰图和热点
    else 采集或分析失败
        Agent->>DB: 写入 FAILED + 真实原因
        Web->>API: 查询失败详情
        API-->>Web: 返回失败阶段和重试建议
        Web-->>User: 显示失败，不伪装成成功
    end
```

---

## 4. AI 循证诊断流程图

```mermaid
flowchart TD
    Q["用户描述：订单服务最近 CPU 很高"]
    Scope{"服务、环境、时间、Agent、PID 是否明确"}
    Ask["AI 追问缺失信息"]
    Context["读取服务拓扑、历史基线、已有任务和告警"]
    Hyp["生成多个可证伪假设"]
    Tree["性能决策树评估下一步"]
    Probe["选择信息增益最高、风险最低的采集器"]
    Risk{"工具是否高风险"}
    Approval["展示命令、参数、风险，等待人工确认"]
    Task["创建 Mini-Drop 真实采集任务"]
    Evidence["导入完成的 TaskAttempt 和可信产物"]
    Judge{"证据支持、反驳还是不足"}
    More{"是否仍有预算和必要性"}
    Report["生成根因、置信度、evidence_refs 和建议"]
    Insufficient["返回信息不足，并说明还缺什么"]
    Verify["修复后重新采集，比较前后快照"]
    Audit["保存全部工具调用、审批、证据和报告版本"]

    Q --> Scope
    Scope -- 否 --> Ask --> Scope
    Scope -- 是 --> Context --> Hyp --> Tree --> Probe --> Risk
    Risk -- 是 --> Approval --> Task
    Risk -- 否 --> Task
    Task --> Evidence --> Judge
    Judge -- 证据充分 --> Report --> Verify --> Audit
    Judge -- 证据不足 --> More
    More -- 继续调查 --> Tree
    More -- 停止 --> Insufficient --> Audit
```

### 性能决策树示意

```mermaid
flowchart TD
    Start["服务变慢"] --> CPU{"CPU 是否异常"}
    CPU -- 高 --> CPUProfile["采集 CPU Profile / 火焰图"]
    CPUProfile --> Hot{"是否存在集中热点"}
    Hot -- 是 --> Code["代码热点 / 锁竞争 / GC 假设"]
    Hot -- 否 --> Neighbor["检查噪声邻居和调度等待"]

    CPU -- 正常 --> MEM{"内存是否持续上涨"}
    MEM -- 是 --> MemData["RSS / PSS / Swap / 分配数据"]
    MemData --> Leak["泄漏、缓存增长或内存压力假设"]

    MEM -- 否 --> IO{"I/O 延迟是否异常"}
    IO -- 是 --> IOData["eBPF I/O 延迟 / 磁盘队列"]
    IOData --> IOReason["慢盘、写放大或共享盘争用"]

    IO -- 否 --> NET{"网络或下游耗时是否异常"}
    NET -- 是 --> Trace["链路、连接、重传、下游指标"]
    Trace --> NetReason["网络问题或真正慢节点"]

    NET -- 否 --> Need["当前证据不足，扩大时间窗或补充上下文"]
```

---

## 5. AI 诊断时序图

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户
    participant Web as AI诊断页面
    participant AI as AI Orchestrator
    participant Guard as 策略与安全门禁
    participant API as Go API / Server
    participant CP as Control Plane
    participant Agent as Agent
    participant Analyzer as Analyzer
    participant Store as DB + MinIO

    User->>Web: 输入“订单服务最近5分钟CPU很高”
    Web->>AI: 创建诊断会话
    AI->>AI: 提取服务、环境、时间、PID

    alt 信息不完整
        AI-->>Web: 返回需要补充的问题
        Web-->>User: 询问环境、实例或PID
        User->>Web: 补充诊断范围
        Web->>AI: 更新上下文
    end

    AI->>Store: 查询历史基线、服务关系和已有证据
    AI->>AI: 生成可证伪假设
    AI-->>Web: 展示假设和选择理由
    AI->>Guard: 请求执行采集工具
    Guard->>Guard: 校验白名单、参数、预算和风险

    alt 高风险操作
        Guard-->>Web: REQUIRE_APPROVAL
        Web-->>User: 展示工具、参数和风险
        User->>Web: 人工批准
        Web->>Guard: 提交批准记录
    end

    Guard->>API: 创建真实采集任务
    API->>CP: 等待调度
    CP->>Agent: 下发任务
    Agent->>Agent: 执行采集
    Agent->>Store: 上传原始产物
    Analyzer->>Store: 读取并分析产物
    Analyzer->>Store: 保存结构化结果
    API-->>AI: 返回完成的 TaskAttempt
    AI->>Store: 校验产物哈希和证据角色
    AI->>AI: 支持或反驳假设

    alt 证据仍不足
        AI-->>Web: 显示缺失证据和下一项探针
        AI->>Guard: 请求下一轮受控采集
    else 证据充分
        AI->>AI: 计算覆盖率和置信度
        AI->>Store: 保存报告版本和 evidence_refs
        AI-->>Web: 返回根因、证据、置信度和建议
        Web-->>User: 展示可追溯报告
    end
```

---

## 6. 一键真实故障 Campaign 流程图

```mermaid
flowchart TD
    Select["用户选择测试场景和诊断策略"]
    Precheck["安全预检<br/>目标、权限、预算、清理脚本"]
    Baseline["采集正常状态基线快照"]
    Inject["执行白名单故障注入代码或命令"]
    Confirm{"故障指标是否达到阈值"}
    Incident["采集故障状态快照"]
    Diagnose["AI 在看不到 Oracle 的情况下诊断"]
    Evidence["生成假设、证据引用和置信度"]
    Oracle["诊断完成后读取隐藏 Oracle"]
    Compare["比较预期根因与实际根因"]
    Cleanup["finally 强制停止故障并清理"]
    Recovery["采集恢复快照并验证恢复"]
    Score["计算根因命中、证据覆盖、恢复和安全分"]
    Fail["记录真实失败原因"]
    Done["保存完整 Campaign 记录"]

    Select --> Precheck --> Baseline --> Inject --> Confirm
    Confirm -- 是 --> Incident --> Diagnose --> Evidence --> Oracle --> Compare --> Cleanup --> Recovery --> Score --> Done
    Confirm -- 否 --> Fail --> Cleanup --> Recovery --> Done
```

### 故障从哪里来

Web 按钮本身不会制造故障，它只是发起一个受控实验。真正的故障由目标主机上的 Agent 执行预先登记的代码或命令产生。

| 场景 | 典型实现 | 观察指标 |
|---|---|---|
| CPU 热点 | 运行计算密集循环或 `stress-ng --cpu` | CPU、火焰图热点 |
| 内存增长 | 持续申请并保留内存 | RSS/PSS/Swap |
| I/O 压力 | `fio` 或受控文件读写 | I/O 延迟、队列深度 |
| 网络延迟 | 网络代理或受控 `tc netem` | RTT、重传、请求耗时 |
| 下游变慢 | 下游接口延迟开关 | 上下游耗时分布 |
| 噪声邻居 | 同宿主机进程争抢 CPU/I/O | 目标与邻居资源对比 |

---

## 7. 真实故障 Campaign 时序图

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户
    participant Web as Web
    participant Eval as Campaign Engine
    participant Guard as 安全门禁
    participant Agent as Agent
    participant Target as 目标服务
    participant AI as AI诊断
    participant Oracle as 隐藏Oracle
    participant Store as 证据存储

    User->>Web: 点击“一键制造故障并评测”
    Web->>Eval: 创建 Campaign
    Eval->>Guard: 安全预检
    Guard-->>Eval: 允许的场景、上限和清理方案

    Eval->>Agent: 采集基线
    Agent->>Target: 读取正常指标
    Agent->>Store: 保存 baseline 快照

    Eval->>Agent: 执行白名单故障注入
    Agent->>Target: 启动 CPU / 内存 / I/O / 网络故障
    Agent->>Target: 检查故障指标

    alt 故障确认成功
        Agent->>Store: 保存 incident 快照
        Eval->>AI: 仅提供问题、环境和真实证据
        AI->>Store: 读取可信证据
        AI->>AI: 假设、反证、决策树诊断
        AI-->>Eval: 根因、置信度、evidence_refs
        Eval->>Oracle: 诊断结束后读取标准答案
        Oracle-->>Eval: 预期根因和证据要求
        Eval->>Eval: 对比命中率和证据覆盖
    else 故障未达到阈值
        Agent-->>Eval: FAULT_NOT_CONFIRMED
        Eval->>Store: 保存真实失败
    end

    Note over Eval,Target: 无论成功还是失败都进入 finally 清理
    Eval->>Agent: 执行清理
    Agent->>Target: 停止故障并恢复配置
    Agent->>Store: 保存 recovery 快照
    Eval->>Eval: 验证恢复门禁
    Eval-->>Web: 展示全过程和最终评分
    Web-->>User: 展示结果，不隐藏失败
```

---

## 8. 多机、多 Agent 诊断架构图

> 多机指多台独立 Linux 主机或虚拟机，不一定是物理服务器。开发阶段也可用虚拟机模拟；Docker 多容器主要用于流程验证，不能完全替代原生多机环境。

```mermaid
flowchart LR
    Web["统一 Web / AI"] --> Server["中心 Server / Control"]

    subgraph MachineA["主机 A：订单服务"]
        AgentA["Agent A"]
        Order["order-service"]
        Neighbor["噪声邻居进程"]
    end

    subgraph MachineB["主机 B：库存服务"]
        AgentB["Agent B"]
        Stock["stock-service"]
    end

    subgraph MachineC["主机 C：数据库"]
        AgentC["Agent C"]
        DB["MySQL / PostgreSQL"]
    end

    Server <--> AgentA
    Server <--> AgentB
    Server <--> AgentC
    Order --> Stock --> DB
    Neighbor -. "争抢 CPU / I/O" .-> Order

    AgentA --> EA["主机A与进程证据"]
    AgentB --> EB["主机B与进程证据"]
    AgentC --> EC["主机C与数据库证据"]
    EA --> AI["跨机证据关联与根因排序"]
    EB --> AI
    EC --> AI
```

### 多机诊断需要回答的问题

1. 订单服务自己的代码是否出现 CPU 热点？
2. 同一主机的噪声邻居是否抢走资源？
3. 订单服务到库存服务之间是否出现网络异常？
4. 库存服务本身是否变慢？
5. 数据库是否存在锁等待、慢查询或连接池耗尽？

---

## 9. 多机诊断时序图

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户
    participant AI as AI诊断
    participant CP as 中心Control
    participant A as 主机A Agent
    participant B as 主机B Agent
    participant C as 主机C Agent
    participant Store as 证据存储

    User->>AI: 服务A变慢，请定位根因
    AI->>AI: 读取服务拓扑 A -> B -> DB
    AI->>CP: 请求主机A进程与宿主机指标
    CP->>A: 采集服务A、系统指标和同机邻居
    A->>Store: 上传A侧证据

    AI->>Store: 分析A侧证据
    alt A自身存在明确热点
        AI->>AI: 提升“服务A代码热点”假设置信度
    else A侧无明确热点
        AI->>CP: 请求A-B网络与主机B证据
        CP->>B: 采集服务B、系统和网络指标
        B->>Store: 上传B侧证据
    end

    AI->>Store: 比较A、B时间窗口和异常开始时间
    alt B服务或网络异常
        AI->>AI: 定位B或A-B通信链路
    else B仍无异常
        AI->>CP: 请求数据库证据
        CP->>C: 采集锁等待、连接和慢查询
        C->>Store: 上传数据库证据
        AI->>Store: 关联数据库证据
    end

    AI->>AI: 按时间先后、因果方向和反证进行根因排序
    AI-->>User: 输出真正根因节点、置信度和跨机证据引用
```

---

## 10. Agent 内部流程图

```mermaid
flowchart TD
    Start["Agent 启动"] --> Register["注册 Agent ID、主机信息和能力"]
    Register --> Heartbeat["每 5 秒发送心跳"]
    Heartbeat --> Claim["轮询或接收任务"]
    Claim --> HasTask{"是否有匹配任务"}
    HasTask -- 否 --> Heartbeat
    HasTask -- 是 --> Validate["检查 PID、采集器、权限、时长和预算"]
    Validate --> Valid{"检查是否通过"}
    Valid -- 否 --> Failed["上报 FAILED 和 reason"]
    Valid -- 是 --> Execute["启动对应 Collector"]
    Execute --> Monitor["监控超时、退出码和资源上限"]
    Monitor --> Result{"执行结果"}
    Result -- 失败 --> Failed
    Result -- 成功 --> Hash["生成产物并计算 SHA256"]
    Hash --> Upload["上传 MinIO"]
    Upload --> Report["上报产物、状态和指标"]
    Report --> Heartbeat
    Failed --> Cleanup["停止子进程并清理临时资源"]
    Cleanup --> Heartbeat
```

### Agent 是不是一个 Python 文件？

不是。Agent 是一个长期运行的系统组件，通常包含：

```text
Agent
├─ 注册与心跳
├─ 任务领取与状态上报
├─ Collector 插件管理
├─ 子进程与超时控制
├─ 产物上传与哈希校验
├─ 故障注入与 finally 清理
└─ 日志与审计
```

它可以用 C++、Go、Rust 或 Python 实现。本项目的重构方向是 C++ 核心 Agent，Python 主要负责 Analyzer 和 AI 诊断。

---

## 11. 汇报时建议展示的三张图

如果时间有限，按以下顺序展示：

1. **项目总体架构图**：说明各语言和组件的职责。
2. **AI 循证诊断流程图**：突出假设、决策树、真实证据和人工审批。
3. **真实故障 Campaign 时序图**：解释测试集不是直接给答案，而是先制造故障、再诊断、最后读取 Oracle 评分并清理。

### 一句话收尾

> Mini-Drop 的基础链路负责真实地采集和分析性能数据，AI 层负责决定下一步查什么并用证据验证假设，Campaign 则负责真实制造故障并检验 AI 是否真的找到了根因。

