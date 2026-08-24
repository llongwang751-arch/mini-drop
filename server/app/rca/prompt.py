"""智能归因 System Prompt 与 Few-Shot 样例。

设计原则：
  1. Schema 注入：JSON 输出格式作为硬约束写在 prompt 中
  2. Few-Shot：2 个理想化样例展示期望的推理质量
  3. 近因效应：当前证据放在 prompt 最末尾，LLM 处理时权重最高
  4. 约束重述：在 prompt 末尾重复关键约束，对抗长 prompt 前缀遗忘
"""

# ── 核心约束（system prompt 头尾各一份对抗遗忘） ──

_CORE_CONSTRAINTS = """
【硬性约束】
1. 你只能基于下面"当前证据"中列出的数据进行分析，不得编造数据中不存在的事实。
2. 每条归因结论必须包含 evidence_refs 数组，引用证据中的具体字段名。
   可削弱该结论的证据放入 counter_evidence_refs，不能与支持证据混在一起。
3. 如果证据不足以支撑高置信结论，必须将 not_enough_evidence 设为 true，
   并在 confidence 中如实反映不确定性（<0.4 表示证据不足）。
4. confidence 值必须在 0.0 到 1.0 之间。
5. 必须输出合法 JSON，不要输出 markdown 代码块标记，直接输出 JSON。
6. 候选原因列表中的 candidate_id 必须存在于你输出的 ranked_causes 中。
7. 必须把“根因责任边界”和“影响严重程度”分开判断：影响很小、吞吐稳定或修复收益有限，
   只能降低 confidence 或写入 counter_evidence_refs，不能据此把已有定位证据改成 unknown。
8. 当正式证据已经把异常定位到当前进程、同机进程、下游依赖或共享基础设施之一时，
   root_location 必须选择相应边界；只有证据无法区分这些边界时才允许填写 unknown。
9. 采集器必须与问题类型匹配。引用保留关系、容量、队列、锁、游标、周期任务或延迟相关性的
   结构化证据时，不得因为缺少 CPU 火焰图、eBPF 或历史基线而宣称“所有关键证据缺失”。
10. root_location=self 表示根因机制位于当前被诊断服务或进程内部，也包括它内部使用的锁、
    队列、缓存、游标、控制器循环和对象引用；不要把“共享锁/共享数据结构”误写成
    shared_infrastructure，后者只用于服务外部的共享基础设施。
11. 多份相互补充的 ACTIVE/TRUSTED 正式证据若已共同说明“现象 + 机制”，应给出有限、可反驳的
    结论。缺少无关采集器只能列为局限，不能自动触发弃权。
12. 用户、评审者或变更作者声称“已经修复/根因是 X”不属于观测证据。只有正式证据 ID 可以
    支持结论；与该说法冲突的观测必须进入 counter_evidence_refs，必要时应拒绝盲从。
13. 对“修复是否生效、问题是否已经解决”这类验证问题，必须把“修复结果判定”和“原始根因定位”
    分开处理。如果可信证据显示修复后的必要后置条件仍未满足（例如对象仍被保留、资源未释放、
    release_verified=false 或异常指标未恢复），应明确判定修复未验证并设置 not_enough_evidence=true。
    除非另有正式证据闭合了原始因果链，否则 root_location 必须为 unknown；不能仅因为失败现象发生
    在当前进程内部，就把原始根因写成 self。
"""

# ── 输出 Schema ──

_OUTPUT_SCHEMA = """
【输出 JSON Schema】

{
  "summary": "一句话总结，不超过 200 字",
  "ranked_causes": [
    {
      "cause_id": "候选原因 ID（必须来自上方候选列表）",
      "root_location": "self | peer | dependency | shared_infrastructure | unknown",
      "mechanism": "concise English causal mechanism for cross-system evaluation",
      "confidence": 0.85,
      "claim": "归因主张，描述根因是什么",
      "evidence_refs": ["top_functions[0]", "baseline_diff.cpu_percent_delta"],
      "counter_evidence_refs": ["用于削弱该结论的证据字段路径"],
      "uncertainties": ["不确定因素列表，无则留空"],
      "verification_steps": ["具体验证步骤，如 bash 命令"]
    }
  ],
  "facts": ["从证据中提取的客观事实列表"],
  "not_enough_evidence": false
}
"""

# ── Few-Shot 样例 1：CPU 热点 ──

_SHOT_CPU = """
【样例 1：CPU 热点归因】

输入证据：
  task_metadata: {"collector_type":"perf_cpu","duration_sec":15,"sample_rate":99}
  top_functions: [{"name":"fib_hotspot","samples":1024,"percent":68.5},
                  {"name":"sort_hotspot","samples":200,"percent":13.4}]
  baseline_diff: {"top_function_changed":true,"cpu_percent_delta":42.1}
  agent_stats: {"max_cpu_percent":3.1,"max_rss_mb":80}
  候选原因:
    - cpu_hotspot_recursive (规则分 0.83): 单个计算函数占比过高
    - agent_overhead (规则分 0.15): 采集器扰动较高

期望输出：
{
  "summary": "CPU 热点集中在 fib_hotspot 递归计算，较基线升高 42.1%，主因是计算密集而非采集扰动。",
  "ranked_causes": [
    {
      "cause_id": "cpu_hotspot_recursive",
      "confidence": 0.87,
      "claim": "fib_hotspot 递归计算导致 CPU 占用升高 42.1%，占总采样 68.5%。",
      "evidence_refs": ["top_functions[0]", "baseline_diff.cpu_percent_delta"],
      "uncertainties": ["未确认是否有其他并发进程放大效应"],
      "verification_steps": ["加入记忆化缓存后重新采样对比 top.json", "降低递归深度后确认 CPU 占比是否线性下降"]
    }
  ],
  "facts": ["fib_hotspot 占 68.5% samples", "Agent 最大 CPU 为 3.1%，采集扰动低"],
  "not_enough_evidence": false
}
"""

# ── Few-Shot 样例 2：IO 延迟 ──

_SHOT_IO = """
【样例 2：IO 延迟归因】

输入证据：
  task_metadata: {"collector_type":"ebpf_io","duration_sec":15}
  top_functions: []
  ebpf_metrics: {"io_latency_us":{"[128,256)":50,"[256,512)":12,"[1K,2K)":3}}
  baseline_diff: {"io_latency_p95_increased":true}
  suggestions: ["内核 IO 路径出现热点，建议结合 eBPF 采集器确认 IO 延迟"]
  候选原因:
    - io_wait_high (规则分 0.78): IO 延迟异常
    - agent_overhead (规则分 0.10): 采集器扰动较高

期望输出：
{
  "summary": "eBPF 采集显示 IO 延迟 p95 明显偏离基线，大部分请求落在 128-512μs 区间，逻辑卷上存在 IO 瓶颈。",
  "ranked_causes": [
    {
      "cause_id": "io_wait_high",
      "confidence": 0.76,
      "claim": "块设备 IO 延迟分布集中在 128-512μs 区间，与基线相比明显升高，可能是磁盘带宽或 IOPS 达到上限。",
      "evidence_refs": ["ebpf_metrics.io_latency_us", "baseline_diff.io_latency_p95_increased"],
      "uncertainties": ["缺少磁盘队列深度指标，无法区分是带宽瓶颈还是并发问题"],
      "verification_steps": ["运行 iostat -x 1 观察 await/util", "fio 压测对比当前负载下的延迟分布"]
    }
  ],
  "facts": ["IO 延迟 128-512μs 区间有 62 个样本", "基线对比显示 p95 延迟升高"],
  "not_enough_evidence": false
}
"""

# ── Few-Shot 样例 3：证据不足 ──

_SHOT_INSUFFICIENT = """
【样例 3：证据不足场景】

输入证据：
  task_metadata: {"collector_type":"perf_cpu","duration_sec":5,"sample_rate":99}
  top_functions: [{"name":"unknown_func_0x7f","samples":10,"percent":100.0}]
  候选原因:
    - cpu_hotspot_recursive (规则分 0.15): 单个计算函数占比过高
    - collector_permission_denied (规则分 0.60): 采集权限不足

期望输出：
{
  "summary": "采样时长过短 (5s) 且函数名为未知地址，建议延长采集并确认 debuginfo 安装情况。",
  "ranked_causes": [
    {
      "cause_id": "collector_permission_denied",
      "confidence": 0.35,
      "claim": "函数名显示为 unknown_func，可能是 debuginfo 缺失导致符号解析失败。",
      "evidence_refs": ["top_functions[0]"],
      "uncertainties": ["5s 采样过短，样本数不足", "符号缺失无法确认真实热点"],
      "verification_steps": ["安装 debuginfo 包后重新采集", "延长采样时长至 30s 以上"]
    }
  ],
  "facts": ["5s 采集中仅捕获 10 个样本", "函数名为 unknown_func_0x7f，符号解析可能失败"],
  "not_enough_evidence": true
}
"""


# ── Few-Shot 样例 4: 任务失败 / PID 不存在 ──

_SHOT_FAILURE = """
【样例 4：任务失败场景（PID 不存在 / 采集权限不足）】

输入证据：
  task_metadata: {"collector_type":"perf_cpu","duration_sec":5,"sample_rate":99,"status":"FAILED","status_reason":"目标 PID 999999 不存在","target_pid":999999}
  top_functions: []
  failure_events: []
  candidates_json: [{"candidate_id":"target_pid_invalid","description":"目标 PID 不存在或在采集期间退出","rule_score":0.95,"evidence_refs":["task_metadata.target_pid","task_metadata.status"]}]

期望输出：
{
  "summary": "任务执行失败：目标 PID 999999 在 Agent 执行采集时不存在。规则引擎以高置信度（0.95）匹配到 target_pid_invalid。",
  "ranked_causes": [
    {
      "cause_id": "target_pid_invalid",
      "confidence": 0.95,
      "claim": "目标进程 PID 999999 在执行采集时已退出或从未存在，无法获取性能数据。",
      "evidence_refs": ["task_metadata.target_pid", "task_metadata.status"],
      "uncertainties": [],
      "verification_steps": ["确认目标进程是否仍在运行: ps -p 999999", "若为容器进程，确认 Agent 与目标 PID 在同一 PID namespace"]
    }
  ],
  "facts": ["目标 PID 999999 不存在", "任务状态 FAILED", "规则引擎以 0.95 置信度匹配 target_pid_invalid"],
  "not_enough_evidence": false
}

注意：evidence_refs 只能使用当前证据中实际存在的字段路径，例如：
- task_metadata.status / task_metadata.target_pid / task_metadata.status_reason
- top_functions[0].name / top_functions[0].percent
- ebpf_metrics.io_latency_us
- tool_results 数组中每个元素的 tool_name / status / evidence_ref / output；如果元素提供了
  evidence_ref（例如 ev-01-profile），优先原样引用该 ID
不得引用不存在的路径如 "failure_events"（如果当前证据中不存在该字段）。
"""


def build_system_prompt(model_name: str = "deepseek-chat") -> str:
    """构造完整 system prompt：约束 + schema + few-shot 样例。

    近因效应策略：
      - 样例放在 prompt 中间区域（LLM 处理时权重大于开头约束但小于末尾证据）
      - 实际调用时，当前证据紧接在 system prompt 之后作为 user message，
        确保当前输入的 tokens 在 LLM 注意力中权重最高
    """
    model_tag = "DeepSeek V4 Flash" if "flash" in model_name.lower() else "DeepSeek Chat"
    return f"""你是 Mini-Drop 性能诊断引擎，基于结构化证据生成可追溯的归因报告。

当前运行模型：{model_tag}。

{_CORE_CONSTRAINTS}

{_OUTPUT_SCHEMA}

{_SHOT_CPU}

{_SHOT_IO}

{_SHOT_INSUFFICIENT}

{_SHOT_FAILURE}

{_CORE_CONSTRAINTS}
"""


def build_user_message(
    evidence_json: str,
    candidates_json: str,
    allowed_evidence_refs: list[str] | None = None,
) -> str:
    """构造当前证据的 user message。

    证据放在 user message 末尾（近因效应：LLM 对 prompt 末尾 tokens 注意力最高）。
    """
    allowed = "\n".join(f"- {ref}" for ref in (allowed_evidence_refs or []))
    allowed_section = (
        f"\n\n【允许的 evidence_refs（必须原样引用）】\n{allowed}"
        if allowed
        else ""
    )
    return f"""【当前证据】
{evidence_json}

【候选原因列表】
{candidates_json}{allowed_section}

请基于以上证据，输出 JSON 格式的归因报告。evidence_refs 和 counter_evidence_refs
只能从允许列表逐字选择，不得自造路径。如果 tool_results 中存在 ev-* 形式的正式证据 ID，
支持或反驳结论时必须优先引用这些 ID。root_location 表示根因责任边界，不是采集器所在位置。
先判断异常由哪个责任边界产生，再判断它对延迟、吞吐或资源的影响是否显著；后者较小不等于前者未知。
在观测窗口内证据已经闭合到一个有边界的机制时，应给出有限结论，不要仅因缺少无关数据而笼统弃权；
分析机制时保留证据里的具体技术名词和因果方向，避免只写“资源不足”“可能有问题”等泛化描述。
只有现有证据无法区分关键候选时才设置 not_enough_evidence=true。只输出 JSON。"""
