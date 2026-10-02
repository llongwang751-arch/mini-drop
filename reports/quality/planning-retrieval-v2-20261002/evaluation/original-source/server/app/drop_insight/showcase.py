"""Mentor-facing completed diagnosis replays.

The data in this module is an explicitly labelled controlled-fault replay. It is
not mixed into production diagnosis records and must never be presented as a
live incident.
"""

from __future__ import annotations


def _noisy_neighbor_io_case() -> dict:
    """Build the cross-process I/O noisy-neighbour replay."""

    return {
        "case_id": "showcase-noisy-neighbor-io-001",
        "title": "订单服务 CPU 升高，但根因在同宿主机日志压缩进程",
        "replay_mode": True,
        "replay_label": "已完成受控故障回放",
        "source": "白名单故障 Campaign：基线、故障、恢复三阶段快照",
        "status": "COMPLETED",
        "symptom": "订单服务 CPU、P99 延迟和磁盘等待同时升高，单看进程指标很像业务代码热点。",
        "root_cause": "同宿主机日志压缩进程集中执行同步写入，推高磁盘队列；订单服务阻塞在 fsync/writeback 路径，CPU 升高是上下文切换和等待放大的伴随现象。",
        "fix": "限制日志压缩并发并错峰执行，降低其 I/O 优先级；业务进程无需改代码。",
        "first_run": {
            "label": "首次诊断：没有可复用 Skill",
            "duration_seconds": 168,
            "tool_calls": 6,
            "confidence": 0.94,
            "explored_branches": 5,
            "pruned_branches": 3,
            "rounds": [
                {
                    "round": 1,
                    "direction": "建立基线",
                    "tool": "系统指标与业务延迟",
                    "duration_seconds": 24,
                    "decision": "CPU、P99 与磁盘等待同窗升高，不能只按 CPU 热点处理",
                    "outcome": "继续并行验证用户态、锁竞争和 I/O 三个方向",
                },
                {
                    "round": 2,
                    "direction": "用户态热点",
                    "tool": "进程 CPU 采样",
                    "duration_seconds": 31,
                    "decision": "用户态样本仅 18%，热点函数不集中",
                    "outcome": "反证成立，剪掉业务代码热点分支",
                },
                {
                    "round": 3,
                    "direction": "锁竞争",
                    "tool": "线程与锁等待检查",
                    "duration_seconds": 27,
                    "decision": "futex 等待占比仅 3.2%，没有长尾锁持有",
                    "outcome": "反证成立，剪掉锁竞争分支并转向 I/O",
                },
                {
                    "round": 4,
                    "direction": "I/O 到宿主机",
                    "tool": "目标 I/O 与同宿主机进程相关性",
                    "duration_seconds": 42,
                    "decision": "目标写入低但整盘队列为 14，log-compactor 写入 228MB/s",
                    "outcome": "跨进程命中噪声邻居，相关系数为 0.93",
                },
                {
                    "round": 5,
                    "direction": "恢复验证",
                    "tool": "同负载恢复窗口复测",
                    "duration_seconds": 44,
                    "decision": "停止故障后磁盘队列、P99 和 CPU 同时回落",
                    "outcome": "因果闭环成立，允许生成候选 Skill",
                },
            ],
            "nodes": [
                {
                    "id": "symptom",
                    "parent_id": None,
                    "domain": "入口",
                    "title": "订单服务 CPU 与 P99 同时升高",
                    "state": "visited",
                    "evidence": "CPU 72% -> 91%，P99 180ms -> 1.8s",
                },
                {
                    "id": "cpu-domain",
                    "parent_id": "symptom",
                    "domain": "CPU",
                    "title": "先验证目标进程自身 CPU",
                    "state": "visited",
                    "tool": "进程 CPU 采样",
                    "evidence": "目标 CPU 高，但用户态样本不足，需要拆分用户态与内核态候选路径",
                },
                {
                    "id": "user-hotspot",
                    "parent_id": "cpu-domain",
                    "domain": "用户态",
                    "title": "业务热点函数",
                    "state": "refuted",
                    "tool": "进程 CPU 采样",
                    "evidence": "用户态样本仅 18%，热点函数不集中；该分支被剪掉",
                },
                {
                    "id": "kernel-hotspot",
                    "parent_id": "cpu-domain",
                    "domain": "内核态",
                    "title": "系统调用或内核热点",
                    "state": "refuted",
                    "tool": "内核栈占比检查",
                    "evidence": "单一内核栈不足 11%，无法单独解释延迟放大",
                },
                {
                    "id": "concurrency-domain",
                    "parent_id": "symptom",
                    "domain": "并发",
                    "title": "检查锁竞争与调度抖动",
                    "state": "visited",
                    "tool": "线程状态与调度指标",
                    "evidence": "线程切换增加，但需要继续区分锁等待和调度排队",
                },
                {
                    "id": "lock",
                    "parent_id": "concurrency-domain",
                    "domain": "锁等待",
                    "title": "锁竞争或线程自旋",
                    "state": "refuted",
                    "tool": "线程与锁等待检查",
                    "evidence": "futex 等待占比 3.2%，无长尾锁持有；该分支被剪掉",
                },
                {
                    "id": "scheduler",
                    "parent_id": "concurrency-domain",
                    "domain": "调度",
                    "title": "CPU 配额或调度排队",
                    "state": "unvisited",
                    "evidence": "锁分支被否定后 I/O 指标已出现强异常，暂不追加调度探针",
                },
                {
                    "id": "io-domain",
                    "parent_id": "symptom",
                    "domain": "I/O",
                    "title": "转向 I/O 等待方向",
                    "state": "visited",
                    "tool": "主机磁盘延迟与队列",
                    "evidence": "磁盘利用率 99%、队列深度 14，与 P99 同窗升高",
                },
                {
                    "id": "target-io",
                    "parent_id": "io-domain",
                    "domain": "目标进程",
                    "title": "目标进程自身 I/O",
                    "state": "refuted",
                    "tool": "进程 I/O 与磁盘延迟检查",
                    "evidence": "目标写入量很低，但磁盘利用率 99%、队列深度 14；问题不在目标自身",
                },
                {
                    "id": "host-contention",
                    "parent_id": "io-domain",
                    "domain": "宿主机",
                    "title": "共享磁盘资源争抢",
                    "state": "visited",
                    "tool": "宿主机进程 I/O 排名",
                    "evidence": "订单进程写入低，必须跨进程寻找真正的磁盘占用者",
                },
                {
                    "id": "peer-scan",
                    "parent_id": "host-contention",
                    "domain": "宿主机",
                    "title": "跨进程扫描同宿主机噪声邻居",
                    "state": "confirmed",
                    "tool": "同宿主机进程相关性检查",
                    "evidence": "log-compactor 写入 228MB/s，与订单 P99 的相关系数 0.93",
                },
                {
                    "id": "recovery",
                    "parent_id": "peer-scan",
                    "domain": "恢复验证",
                    "title": "停止故障并同负载复测",
                    "state": "confirmed",
                    "tool": "恢复窗口系统指标",
                    "evidence": "磁盘队列 14 -> 1.2，P99 1.8s -> 205ms，CPU 回落至 74%",
                },
                {
                    "id": "disk-error",
                    "parent_id": "io-domain",
                    "domain": "块设备",
                    "title": "磁盘错误或设备降级",
                    "state": "unvisited",
                    "evidence": "噪声邻居与恢复窗口已形成闭环，停止扩展设备故障分支",
                },
                {
                    "id": "network",
                    "parent_id": "symptom",
                    "domain": "网络",
                    "title": "下游网络异常",
                    "state": "unvisited",
                    "evidence": "已有 I/O 根因闭环，满足停止条件，未继续调用网络工具",
                },
                {
                    "id": "downstream-loss",
                    "parent_id": "network",
                    "domain": "下游",
                    "title": "重传、丢包或连接超时",
                    "state": "unvisited",
                    "evidence": "父分支未进入，保留为下一轮候选而不浪费工具调用",
                },
                {
                    "id": "memory",
                    "parent_id": "symptom",
                    "domain": "内存",
                    "title": "回收、缺页或内存带宽争抢",
                    "state": "unvisited",
                    "evidence": "当前证据已满足 I/O 根因停止条件，本轮未调查",
                },
            ],
            "switches": [
                {"from": "用户态", "to": "并发", "reason": "perf 样本未形成业务热点"},
                {"from": "并发", "to": "I/O", "reason": "锁等待占比不足以解释 P99"},
                {"from": "目标进程 I/O", "to": "宿主机", "reason": "目标写入低但整盘拥塞"},
            ],
            "evidence_refs": [
                "snapshot:baseline/order-host",
                "snapshot:fault/order-host",
                "task:process-cpu-profile",
                "task:host-io-correlation",
                "snapshot:recovery/order-host",
            ],
        },
        "generated_skill": {
            "candidate_id": "skill_candidate_io_neighbor_v1",
            "source_report_id": "report_showcase_noisy_neighbor_001",
            "name": "同宿主机 I/O 噪声邻居循证诊断",
            "version": "v1",
            "status": "已通过三类门禁，等待人工发布",
            "action": "新建候选 Skill",
            "source_case": "showcase-noisy-neighbor-io-001",
            "source_trace": "首次诊断的 5 轮实际探索轨迹，不使用初始预设树代替",
            "probe_order": ["系统与 P99 基线", "目标进程 CPU 采样", "锁等待", "目标 I/O", "同宿主机相关性", "恢复复测"],
            "negative_paths": ["无用户态热点时停止深挖业务函数", "锁等待低时停止锁竞争分支", "目标写入低而整盘忙时转向宿主机"],
            "switch_conditions": ["用户态样本 < 30%", "futex 等待 < 10%", "目标写入低且磁盘利用率 > 90%"],
            "evidence_requirements": ["基线、故障、恢复三段快照", "同宿主机写入与业务 P99 相关系数 > 0.8", "故障停止后两项指标同时回落"],
            "stop_conditions": ["根因证据、反证和恢复验证同时满足", "未满足证据门槛时必须拒绝建立根因"],
            "gate_results": [
                {"name": "相似正例", "result": "PASS", "passed": 5, "total": 5},
                {"name": "相似症状反例", "result": "PASS", "passed": 4, "total": 4},
                {"name": "环境迁移", "result": "PASS", "passed": 2, "total": 2},
            ],
        },
        "second_run": {
            "label": "第二次：相似但不完全相同的故障",
            "incident": "另一台主机的备份压缩进程造成同类磁盘拥塞",
            "skill_hit": True,
            "duration_seconds": 62,
            "tool_calls": 3,
            "confidence": 0.92,
            "root_cause_correct": True,
            "path": ["系统与 P99 基线", "目标 I/O 与整盘对比", "同宿主机相关性与恢复复测"],
        },
        "counterexample": {
            "label": "第三次：症状相似但根因不同",
            "incident": "下游网络丢包导致 P99 和 CPU 同时升高",
            "skill_considered": True,
            "skill_applied": False,
            "duration_seconds": 74,
            "tool_calls": 4,
            "root_cause": "下游重传与连接超时",
            "rejection_reason": "磁盘队列与同宿主机写入均正常，不满足 Skill 的必要证据门槛",
            "false_transfer": False,
        },
        "comparison": {
            "before": {"tool_calls": 6, "duration_seconds": 168, "invalid_branches": 3, "confidence": 0.94},
            "after": {"tool_calls": 3, "duration_seconds": 62, "invalid_branches": 0, "confidence": 0.92},
            "improvement": {"tool_calls_percent": 50, "duration_percent": 63, "false_transfer_rate": 0},
        },
    }


def _python_native_oversubscription_case() -> dict:
    """Build a replay where Python symptoms lead to a native runtime root cause."""

    return {
        "case_id": "showcase-python-native-oversubscription-002",
        "short_title": "Python CPU：原生线程过量",
        "title": "推荐服务 Python CPU 升高，根因是 OpenBLAS 线程过量",
        "replay_mode": True,
        "replay_label": "已完成受控故障回放",
        "source": "白名单故障 Campaign：Python 负载、原生栈与恢复窗口",
        "status": "COMPLETED",
        "symptom": "Python 进程 CPU 接近 95%，P99 升高，看起来像用户态热点或 GIL 竞争。",
        "root_cause": "NumPy 调用 OpenBLAS 时每个请求扩张 32 个原生线程，请求并发叠加后造成 CPU 过度订阅；Python 栈中的业务函数只是入口。",
        "fix": "将 OPENBLAS_NUM_THREADS 从 32 限制为 4，并按 CPU 配额限制请求并发；业务算法保持不变。",
        "first_run": {
            "label": "首次诊断：从 Python 栈跨到原生运行时",
            "duration_seconds": 182,
            "tool_calls": 7,
            "confidence": 0.93,
            "explored_branches": 6,
            "pruned_branches": 3,
            "rounds": [
                {"round": 1, "direction": "建立基线", "tool": "系统指标与请求延迟", "duration_seconds": 22, "decision": "CPU 95%、P99 1.4s，线程数从 18 增至 146", "outcome": "并行验证 Python 热点、GIL 和原生栈"},
                {"round": 2, "direction": "Python 用户态", "tool": "py-spy 调用栈", "duration_seconds": 28, "decision": "Python 帧仅占 21%，没有单一业务函数超过 30%", "outcome": "剪掉纯 Python 热点分支"},
                {"round": 3, "direction": "GIL 与调度", "tool": "线程状态与 GIL 等待", "duration_seconds": 25, "decision": "GIL 等待占比 6%，大量线程运行在 native 状态", "outcome": "剪掉 GIL 竞争分支，转向原生栈"},
                {"round": 4, "direction": "原生 CPU 栈", "tool": "perf 原生栈采样", "duration_seconds": 36, "decision": "libopenblas dgemm 内核占 68%，业务 Python 帧只是调用入口", "outcome": "锁定数值计算线程池方向"},
                {"round": 5, "direction": "配置关联", "tool": "线程数与环境变量", "duration_seconds": 31, "decision": "OPENBLAS_NUM_THREADS=32，线程增长与并发请求强相关", "outcome": "确认 CPU 过度订阅机制"},
                {"round": 6, "direction": "恢复验证", "tool": "同负载限线程复测", "duration_seconds": 40, "decision": "限制为 4 线程后 CPU 95% -> 63%，P99 1.4s -> 280ms", "outcome": "因果闭环成立，生成候选 Skill"},
            ],
            "nodes": [
                {"id": "symptom", "parent_id": None, "domain": "入口", "title": "Python CPU 与 P99 同时升高", "state": "visited", "evidence": "CPU 95%，P99 1.4s，线程数 18 -> 146"},
                {"id": "python-hotspot", "parent_id": "symptom", "domain": "Python", "title": "怀疑纯 Python 热点", "state": "refuted", "tool": "py-spy 调用栈", "evidence": "Python 帧仅占 21%，无集中热点，分支被剪掉"},
                {"id": "gil", "parent_id": "symptom", "domain": "并发", "title": "怀疑 GIL 竞争", "state": "refuted", "tool": "线程状态检查", "evidence": "GIL 等待仅 6%，大量线程处于 native 状态"},
                {"id": "native-stack", "parent_id": "symptom", "domain": "原生栈", "title": "转向 C 扩展原生栈", "state": "visited", "tool": "perf 原生栈采样", "evidence": "libopenblas dgemm 占 CPU 样本 68%"},
                {"id": "oversubscription", "parent_id": "native-stack", "domain": "运行时配置", "title": "确认 OpenBLAS 线程过量", "state": "confirmed", "tool": "环境变量与线程相关性", "evidence": "每请求 32 个原生线程，并发叠加造成过度订阅"},
                {"id": "recovery", "parent_id": "oversubscription", "domain": "恢复验证", "title": "限制线程并同负载复测", "state": "confirmed", "tool": "恢复窗口指标", "evidence": "CPU 降至 63%，P99 恢复至 280ms"},
                {"id": "io", "parent_id": "symptom", "domain": "I/O", "title": "磁盘或网络等待", "state": "unvisited", "evidence": "原生线程过量已形成因果闭环，满足停止条件"},
            ],
            "switches": [
                {"from": "Python 用户态", "to": "并发", "reason": "py-spy 未形成集中业务热点"},
                {"from": "并发", "to": "原生栈", "reason": "GIL 等待低且线程集中在 native 状态"},
                {"from": "原生栈", "to": "运行时配置", "reason": "OpenBLAS 热点需要结合线程配置解释"},
            ],
            "evidence_refs": ["snapshot:baseline/python-host", "task:pyspy-profile", "task:native-perf-profile", "config:openblas-threads", "snapshot:recovery/python-host"],
        },
        "generated_skill": {
            "candidate_id": "skill_candidate_python_native_v1", "source_report_id": "report_showcase_python_native_002",
            "name": "Python 原生扩展线程过量循证诊断", "version": "v1", "status": "已通过三类门禁，等待人工发布", "action": "新建候选 Skill",
            "source_case": "showcase-python-native-oversubscription-002", "source_trace": "首次诊断的 6 轮跨语言探索轨迹",
            "probe_order": ["系统与线程基线", "py-spy", "GIL 与线程状态", "perf 原生栈", "运行时配置", "恢复复测"],
            "negative_paths": ["Python 帧占比低时停止深挖业务函数", "GIL 等待低时转向原生栈", "原生热点必须关联线程配置"],
            "switch_conditions": ["Python 帧 < 30%", "GIL 等待 < 10%", "native 线程数显著超过 CPU 配额"],
            "evidence_requirements": ["Python 与原生栈双视角", "线程配置和负载相关性", "限线程后的恢复窗口"],
            "stop_conditions": ["原生热点、配置机制和恢复验证同时成立", "只有 Python 入口帧时不得归因业务代码"],
            "gate_results": [{"name": "相似正例", "result": "PASS", "passed": 5, "total": 5}, {"name": "相似症状反例", "result": "PASS", "passed": 4, "total": 4}, {"name": "环境迁移", "result": "PASS", "passed": 3, "total": 3}],
        },
        "second_run": {"label": "第二次：NumPy 批处理服务出现同类过载", "incident": "另一服务的 MKL 线程池超过容器 CPU 配额", "skill_hit": True, "duration_seconds": 71, "tool_calls": 4, "confidence": 0.91, "root_cause_correct": True, "path": ["系统与线程基线", "Python/原生栈对比", "线程配置与恢复复测"]},
        "counterexample": {"label": "第三次：Python CPU 高但根因不同", "incident": "纯 Python JSON 序列化热点", "skill_considered": True, "skill_applied": False, "duration_seconds": 66, "tool_calls": 3, "root_cause": "业务序列化函数形成 74% 集中热点", "rejection_reason": "Python 帧高度集中，不满足原生线程过量 Skill 的必要条件", "false_transfer": False},
        "comparison": {"before": {"tool_calls": 7, "duration_seconds": 182, "invalid_branches": 3, "confidence": 0.93}, "after": {"tool_calls": 4, "duration_seconds": 71, "invalid_branches": 0, "confidence": 0.91}, "improvement": {"tool_calls_percent": 43, "duration_percent": 61, "false_transfer_rate": 0}},
    }


def _database_lock_chain_case() -> dict:
    """Build a replay where an API symptom is traced to a remote lock owner."""

    return {
        "case_id": "showcase-database-lock-chain-003",
        "short_title": "数据库锁：跨服务阻塞链",
        "title": "支付接口超时，根因是批处理服务遗留长事务",
        "replay_mode": True,
        "replay_label": "已完成受控故障回放",
        "source": "白名单故障 Campaign：锁等待、连接池与恢复窗口",
        "status": "COMPLETED",
        "symptom": "支付接口 P99 升至 2.6s，数据库 CPU 82%，连接池接近耗尽。",
        "root_cause": "结算批处理服务异常路径遗漏回滚，留下 idle in transaction 会话并持有订单索引锁，支付查询排队后耗尽连接池。",
        "fix": "回滚长事务、修复异常路径的事务关闭逻辑，并增加 idle_in_transaction_session_timeout。",
        "first_run": {
            "label": "首次诊断：从接口超时追到跨服务锁持有者",
            "duration_seconds": 175, "tool_calls": 7, "confidence": 0.95, "explored_branches": 16, "pruned_branches": 6,
            "rounds": [
                {"round": 1, "direction": "建立基线", "tool": "接口与数据库指标", "duration_seconds": 20, "decision": "P99、活跃会话和连接池等待同窗升高", "outcome": "验证慢 SQL、连接池和锁链"},
                {"round": 2, "direction": "查询计划", "tool": "慢 SQL 与执行计划", "duration_seconds": 29, "decision": "执行计划与基线一致，单次执行仅 18ms", "outcome": "剪掉计划回归分支"},
                {"round": 3, "direction": "连接池", "tool": "池等待与借还记录", "duration_seconds": 24, "decision": "连接归还逻辑正常，但请求都阻塞在同一数据库等待事件", "outcome": "连接池耗尽是结果，转向数据库锁"},
                {"round": 4, "direction": "锁等待图", "tool": "阻塞会话与锁图", "duration_seconds": 35, "decision": "47 个支付会话被同一 idle in transaction 会话阻塞", "outcome": "沿会话来源追查锁持有服务"},
                {"round": 5, "direction": "跨服务归属", "tool": "连接标签与发布记录", "duration_seconds": 39, "decision": "阻塞会话来自结算批处理，异常路径遗漏 rollback", "outcome": "确认跨服务长事务根因"},
                {"round": 6, "direction": "恢复验证", "tool": "回滚事务后同负载复测", "duration_seconds": 28, "decision": "锁队列 47 -> 0，P99 2.6s -> 190ms，连接池恢复", "outcome": "因果闭环成立，生成候选 Skill"},
            ],
            "nodes": [
                {"id": "symptom", "parent_id": None, "domain": "入口", "title": "支付 P99 与数据库 CPU 同升", "state": "visited", "evidence": "P99 2.6s，数据库 CPU 82%，连接池等待 47"},
                {"id": "application", "parent_id": "symptom", "domain": "应用层", "title": "先排查支付服务自身", "state": "visited", "tool": "应用指标与线程状态", "evidence": "请求线程大量等待数据库，应用 CPU 热点不集中"},
                {"id": "app-hotspot", "parent_id": "application", "domain": "代码热点", "title": "业务代码计算热点", "state": "refuted", "tool": "应用 CPU 采样", "evidence": "业务函数样本分散，无法解释 2.6s 长尾"},
                {"id": "thread-pool", "parent_id": "application", "domain": "线程池", "title": "线程池配置不足", "state": "refuted", "tool": "线程状态分布", "evidence": "线程池仍有余量，大量线程停在数据库调用边界"},
                {"id": "database", "parent_id": "symptom", "domain": "数据库", "title": "进入数据库侧分解等待来源", "state": "visited", "tool": "数据库会话与等待事件", "evidence": "活跃会话和连接等待同窗升高，主要等待类型为锁"},
                {"id": "query", "parent_id": "database", "domain": "SQL", "title": "验证慢 SQL 与执行计划", "state": "visited", "tool": "慢 SQL 与执行计划", "evidence": "SQL 文本命中，但需要区分执行慢和排队慢"},
                {"id": "query-plan", "parent_id": "query", "domain": "执行计划", "title": "查询计划回归", "state": "refuted", "tool": "基线计划对比", "evidence": "计划未变化，脱离锁等待后单次执行仅 18ms"},
                {"id": "missing-index", "parent_id": "query", "domain": "索引", "title": "缺失索引导致全表扫描", "state": "refuted", "tool": "扫描行数与索引命中", "evidence": "索引命中率稳定，扫描行数与历史基线一致"},
                {"id": "pool", "parent_id": "database", "domain": "连接池", "title": "检查连接池为何耗尽", "state": "visited", "tool": "连接池借还记录", "evidence": "连接等待 47，但借出连接均阻塞在同一数据库等待事件"},
                {"id": "pool-leak", "parent_id": "pool", "domain": "连接泄漏", "title": "连接借出后未归还", "state": "refuted", "tool": "连接借还生命周期", "evidence": "借还逻辑正常；池耗尽是上游请求排队的结果"},
                {"id": "pool-saturation", "parent_id": "pool", "domain": "表象", "title": "连接池容量耗尽", "state": "visited", "tool": "池等待与会话关联", "evidence": "确认是故障放大器，但不是第一根因，继续追踪统一等待事件"},
                {"id": "lock-graph", "parent_id": "database", "domain": "数据库锁", "title": "构建阻塞会话图", "state": "visited", "tool": "数据库锁图", "evidence": "47 个支付会话收敛到同一锁持有会话"},
                {"id": "ddl-lock", "parent_id": "lock-graph", "domain": "元数据锁", "title": "DDL 或发布操作持锁", "state": "refuted", "tool": "锁类型与发布窗口", "evidence": "无 DDL 会话，锁类型为订单索引行锁"},
                {"id": "row-lock", "parent_id": "lock-graph", "domain": "行锁", "title": "沿行锁追踪阻塞链", "state": "visited", "tool": "阻塞 PID 与事务快照", "evidence": "47 个等待者均指向 idle in transaction 会话"},
                {"id": "lock-owner", "parent_id": "row-lock", "domain": "会话归属", "title": "识别锁持有者来源", "state": "visited", "tool": "连接标签与服务拓扑", "evidence": "会话标签指向结算批处理服务，而非支付服务"},
                {"id": "batch-owner", "parent_id": "lock-owner", "domain": "跨服务", "title": "定位结算批处理长事务", "state": "confirmed", "tool": "连接标签与发布记录", "evidence": "批处理异常发生后事务持续 19 分钟未结束"},
                {"id": "transaction-path", "parent_id": "batch-owner", "domain": "代码路径", "title": "确认异常路径遗漏回滚", "state": "confirmed", "tool": "事务日志与异常路径", "evidence": "超时分支提前返回，未执行 rollback/close"},
                {"id": "recovery", "parent_id": "transaction-path", "domain": "恢复验证", "title": "回滚长事务并同负载复测", "state": "confirmed", "tool": "恢复窗口数据库指标", "evidence": "锁队列 47 -> 0，P99 2.6s -> 190ms，连接池恢复"},
                {"id": "network", "parent_id": "symptom", "domain": "网络", "title": "数据库代理或下游网络抖动", "state": "unvisited", "evidence": "锁等待已形成因果闭环，达到停止条件"},
                {"id": "proxy-timeout", "parent_id": "network", "domain": "数据库代理", "title": "代理丢包与连接超时", "state": "unvisited", "evidence": "父方向未进入，保留为反例测试分支"},
                {"id": "host", "parent_id": "symptom", "domain": "宿主机", "title": "CPU、内存或磁盘资源争抢", "state": "unvisited", "evidence": "数据库锁证据充分，未继续扩大采集范围"},
                {"id": "host-io", "parent_id": "host", "domain": "磁盘", "title": "宿主机 I/O 拥塞", "state": "unvisited", "evidence": "数据库磁盘延迟处于基线范围"},
            ],
            "switches": [
                {"from": "应用层", "to": "数据库", "reason": "应用线程集中等待数据库，业务 CPU 热点被否定"},
                {"from": "SQL", "to": "连接池", "reason": "执行计划稳定且脱离等待后执行正常"},
                {"from": "连接池", "to": "数据库锁", "reason": "池耗尽只是统一锁等待造成的表象"},
                {"from": "数据库锁", "to": "跨服务", "reason": "阻塞会话属于其他服务，必须追踪真实锁持有者"},
                {"from": "跨服务", "to": "代码路径", "reason": "需要解释长事务为何没有正常提交或回滚"},
            ],
            "evidence_refs": ["snapshot:baseline/payment-db", "task:query-plan", "task:database-lock-graph", "trace:connection-owner", "snapshot:recovery/payment-db"],
        },
        "generated_skill": {
            "candidate_id": "skill_candidate_db_lock_chain_v1", "source_report_id": "report_showcase_db_lock_003", "name": "跨服务数据库锁阻塞循证诊断", "version": "v1", "status": "已通过三类门禁，等待人工发布", "action": "新建候选 Skill", "source_case": "showcase-database-lock-chain-003", "source_trace": "首次诊断的 6 轮跨服务探索轨迹",
            "probe_order": ["接口与数据库基线", "执行计划", "连接池", "锁等待图", "连接归属", "恢复复测"],
            "negative_paths": ["计划稳定时停止优化 SQL", "借还正常时不把池耗尽当根因", "锁图必须继续追到会话拥有者"],
            "switch_conditions": ["执行计划与基线一致", "多个请求共享同一等待事件", "阻塞会话来自其他服务"],
            "evidence_requirements": ["阻塞会话图", "连接来源与事务代码路径", "回滚后的恢复窗口"],
            "stop_conditions": ["锁持有者、代码路径和恢复验证同时满足", "只看到连接池耗尽时不得结束诊断"],
            "gate_results": [{"name": "相似正例", "result": "PASS", "passed": 5, "total": 5}, {"name": "相似症状反例", "result": "PASS", "passed": 5, "total": 5}, {"name": "环境迁移", "result": "PASS", "passed": 2, "total": 2}],
        },
        "second_run": {"label": "第二次：库存服务出现同类锁链", "incident": "库存补偿任务持锁导致在线请求排队", "skill_hit": True, "duration_seconds": 68, "tool_calls": 4, "confidence": 0.93, "root_cause_correct": True, "path": ["接口与数据库基线", "锁等待图", "会话归属与恢复复测"]},
        "counterexample": {"label": "第三次：连接池耗尽但无锁等待", "incident": "数据库代理网络超时导致连接回收变慢", "skill_considered": True, "skill_applied": False, "duration_seconds": 79, "tool_calls": 4, "root_cause": "数据库代理丢包与连接超时", "rejection_reason": "锁图为空，不满足数据库锁 Skill 的必要证据门槛", "false_transfer": False},
        "comparison": {"before": {"tool_calls": 7, "duration_seconds": 175, "invalid_branches": 3, "confidence": 0.95}, "after": {"tool_calls": 4, "duration_seconds": 68, "invalid_branches": 0, "confidence": 0.93}, "improvement": {"tool_calls_percent": 43, "duration_percent": 61, "false_transfer_rate": 0}},
    }


def get_mentor_complex_showcase() -> dict:
    """Return completed multi-round replays used to explain Skill evolution."""

    cases = [
        _noisy_neighbor_io_case(),
        _python_native_oversubscription_case(),
        _database_lock_chain_case(),
    ]
    # Keep the original top-level shape for older clients while exposing the
    # complete case library to newer UIs.
    return {**cases[0], "default_case_id": cases[0]["case_id"], "cases": cases}


def _tool_name(round_item: dict) -> str:
    text = f"{round_item.get('direction', '')} {round_item.get('tool', '')}".lower()
    if "py-spy" in text or "python" in text:
        return "start_pyspy_profile"
    if "perf" in text or "cpu" in text or "原生栈" in text:
        return "start_perf_profile"
    if "i/o" in text or "io" in text or "磁盘" in text:
        return "start_ebpf_io_profile"
    if "数据库" in text or "锁" in text or "sql" in text or "连接池" in text:
        return "collect_database_diagnostics"
    return "collect_sys_metrics"


def _showcase_history_payload(case: dict) -> dict:
    """Project a completed replay into the same resources as a diagnosis record."""

    first_run = case["first_run"]
    rounds = first_run["rounds"]
    hypotheses = []
    tool_calls = []
    evidence = []
    events = []
    for index, round_item in enumerate(rounds, start=1):
        outcome = str(round_item.get("outcome", ""))
        is_final = index == len(rounds)
        is_refuted = "剪" in outcome or "排除" in outcome or "结果" in outcome
        status = "SUPPORTED" if is_final else "COUNTER" if is_refuted else "INCONCLUSIVE"
        hypothesis_id = f"{case['case_id']}:hypothesis:{index}"
        tool_call_id = f"{case['case_id']}:tool:{index}"
        evidence_id = f"{case['case_id']}:evidence:{index}"
        hypotheses.append(
            {
                "hypothesis_id": hypothesis_id,
                "round_index": index,
                "statement": round_item["direction"],
                "status": status,
                "source": "MODEL" if index == 1 else "MODEL_REPLAN",
                "generation_reason": round_item["decision"],
                "expected_observations": [round_item["decision"]],
                "falsification_criteria": [outcome],
            }
        )
        tool_name = _tool_name(round_item)
        tool_calls.append(
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": "COMPLETED",
                "policy_decision": "受控回放",
                "policy_reason": f"第 {index} 轮：{round_item['tool']}",
                "arguments_json": {
                    "agent_id": "showcase-agent",
                    "pid": 31042,
                    "duration_seconds": round_item["duration_seconds"],
                },
                "created_at": f"2026-08-26T10:{index:02d}:00+08:00",
            }
        )
        role = "SUPPORT" if is_final else "COUNTER" if is_refuted else "NEUTRAL"
        decision = {
            "SUPPORT": "ACCEPT_SUPPORT",
            "COUNTER": "ACCEPT_COUNTER",
            "NEUTRAL": "ACCEPT_NEUTRAL",
        }[role]
        evidence.append(
            {
                "evidence_id": evidence_id,
                "hypothesis_id": hypothesis_id,
                "role": role,
                "classification": {"decision": decision},
                "envelope": {
                    "source": {"tool_name": round_item["tool"]},
                    "observation": {"metadata": {"summary": round_item["decision"]}},
                },
            }
        )
        events.extend(
            [
                {
                    "event_id": f"{case['case_id']}:round:{index}:planned",
                    "event_type": "diagnosis.hypothesis_planned",
                    "created_at": f"2026-08-26T10:{index:02d}:00+08:00",
                    "payload_json": {"round": index, "direction": round_item["direction"]},
                },
                {
                    "event_id": f"{case['case_id']}:round:{index}:decided",
                    "event_type": "diagnosis.evidence_classified",
                    "created_at": f"2026-08-26T10:{index:02d}:30+08:00",
                    "payload_json": {"round": index, "decision": outcome},
                },
            ]
        )

    report = {
        "report_id": case["generated_skill"]["source_report_id"],
        "hypothesis_id": hypotheses[-1]["hypothesis_id"],
        "version": 1,
        "conclusion": (
            f"**最终根因**：{case['root_cause']}\n\n"
            f"**处理与验证**：{case['fix']}\n\n"
            f"首次诊断经过 {len(rounds)} 轮、调用 {first_run['tool_calls']} 次工具，"
            f"其中剪枝 {first_run['pruned_branches']} 条路径，最终完成恢复反证。"
        ),
        "confidence": first_run["confidence"],
        "verification": {"status": "VERIFIED"},
        "evidence_refs": first_run["evidence_refs"],
        "counter_evidence_refs": [
            item["evidence_id"] for item in evidence if item["role"] == "COUNTER"
        ],
        "exploration_nodes": first_run["nodes"],
        "exploration_switches": first_run["switches"],
        "generated_skill": case["generated_skill"],
        "created_at": "2026-08-26T10:20:00+08:00",
    }
    native = {
        "diagnosis_id": case["case_id"],
        "query": f"【导师演示·{len(rounds)}轮】{case['title']}",
        "status": "COMPLETED",
        "classification": "受控故障 · 多轮循证诊断",
        "target": {"agent_id": "showcase-agent", "pid": 31042},
        "time_range": {"mode": "受控故障回放"},
        "provenance": {
            "kind": "CONTROLLED_REPLAY",
            "live_collection": False,
            "description": "固定受控案例，用于验证多轮交互、剪枝和证据展示；不代表线上事故或生产准确率。",
        },
        "created_at": "2026-08-26T10:00:00+08:00",
        "updated_at": "2026-08-26T10:20:00+08:00",
        "showcase": case,
        "hypotheses": hypotheses,
        "tool_calls": tool_calls,
        "evidence": evidence,
        "events": events,
        "reports": [report],
    }
    return {
        "case_id": case["case_id"],
        "diagnosis_id": case["case_id"],
        "source": "controlled_showcase",
        "strategy": "MULTI_ROUND_EXPLORATION_TREE",
        "query": native["query"],
        "status": "COMPLETED",
        "canonical_status": "COMPLETED",
        "target": native["target"],
        "time_range": native["time_range"],
        "budget": {},
        "hypothesis_count": len(hypotheses),
        "evidence_count": len(evidence),
        "report_version_count": 1,
        "task_ids": [],
        "created_at": native["created_at"],
        "updated_at": native["updated_at"],
        "native_payload": native,
    }


def list_showcase_diagnostic_cases(*, include_native: bool = False) -> list[dict]:
    cases = [_showcase_history_payload(case) for case in get_mentor_complex_showcase()["cases"]]
    if include_native:
        return cases
    return [{key: value for key, value in item.items() if key != "native_payload"} for item in cases]


def get_showcase_diagnostic_case(case_id: str) -> dict | None:
    return next(
        (item for item in list_showcase_diagnostic_cases(include_native=True) if item["case_id"] == case_id),
        None,
    )
