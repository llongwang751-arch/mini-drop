"""Allow-listed tools visible to the Drop Insight planner.

The model sees semantic diagnostic tools, while the executor only receives a
TaskKind from :data:`TOOL_TO_COLLECTOR`.  Keeping that mapping here prevents
the planner, policy and executor from drifting into three different catalogs.
"""

TOOLS = [
    {
        "name": "get_agent_status",
        "version": "1.0",
        "description": "读取 Agent 心跳、能力与资源开销",
        "risk_level": "R0",
        "requires_approval": False,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["agent_id"],
            "properties": {"agent_id": {"type": "string", "minLength": 1}},
        },
    },
    {
        "name": "collect_sys_metrics",
        "version": "1.0",
        "description": "采集主机与目标进程的低开销系统指标",
        "risk_level": "R1",
        "requires_approval": False,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["agent_id", "pid", "duration_seconds"],
            "properties": {
                "agent_id": {"type": "string", "minLength": 1},
                "pid": {"type": "integer", "minimum": 1},
                "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
            },
        },
    },
    {
        "name": "collect_database_diagnostics",
        "version": "1.0",
        "description": "只读采集 PostgreSQL 锁等待、阻塞关系与事务等待时长（不采集 SQL 正文）",
        "risk_level": "R1",
        "requires_approval": False,
        "required_capabilities": ["database_lock"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["agent_id", "pid", "duration_seconds"],
            "properties": {
                "agent_id": {"type": "string", "minLength": 1},
                "pid": {"type": "integer", "minimum": 1},
                "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
            },
        },
    },
    {
        "name": "start_perf_profile",
        "version": "1.0",
        "description": "对指定 Linux PID 执行 CPU Profile",
        "risk_level": "R2",
        "requires_approval": True,
        "required_capabilities": ["perf_cpu"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["agent_id", "pid", "duration_seconds", "sample_rate"],
            "properties": {
                "agent_id": {"type": "string", "minLength": 1},
                "pid": {"type": "integer", "minimum": 1},
                "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
                "sample_rate": {"type": "integer", "minimum": 1, "maximum": 999},
            },
        },
    },
    {
        "name": "start_ebpf_io_profile",
        "version": "1.0",
        "description": "Collect Linux kernel IO latency distribution with eBPF.",
        "risk_level": "R2",
        "requires_approval": True,
        "required_capabilities": ["ebpf_io"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["agent_id", "pid", "duration_seconds"],
            "properties": {
                "agent_id": {"type": "string", "minLength": 1},
                "pid": {"type": "integer", "minimum": 1},
                "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
            },
        },
    },
    {
        "name": "start_pyspy_profile",
        "version": "1.0",
        "description": "Collect Python user-space stacks with py-spy.",
        "risk_level": "R2",
        "requires_approval": True,
        "required_capabilities": ["pyspy"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["agent_id", "pid", "duration_seconds", "sample_rate"],
            "properties": {
                "agent_id": {"type": "string", "minLength": 1},
                "pid": {"type": "integer", "minimum": 1},
                "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
                "sample_rate": {"type": "integer", "minimum": 1, "maximum": 999},
            },
        },
    },
    {
        "name": "start_jvm_profile",
        "version": "1.0",
        "description": "Collect JVM CPU, allocation, lock or wall-clock stacks with async-profiler.",
        "risk_level": "R2",
        "requires_approval": True,
        "required_capabilities": ["java_async"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["agent_id", "pid", "duration_seconds", "sample_rate"],
            "properties": {
                "agent_id": {"type": "string", "minLength": 1},
                "pid": {"type": "integer", "minimum": 1},
                "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
                "sample_rate": {"type": "integer", "minimum": 1, "maximum": 999},
                "event": {
                    "type": "string",
                    "enum": ["cpu", "alloc", "lock", "wall"],
                },
            },
        },
    },
    {
        "name": "collect_memory_profile",
        "version": "1.0",
        "description": "Sample RSS, PSS and swap from the bound Linux process.",
        "risk_level": "R1",
        "requires_approval": False,
        "required_capabilities": ["memory_smaps"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["agent_id", "pid", "duration_seconds"],
            "properties": {
                "agent_id": {"type": "string", "minLength": 1},
                "pid": {"type": "integer", "minimum": 1},
                "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
            },
        },
    },
    {
        "name": "collect_go_profile",
        "version": "1.0",
        "description": "Collect a Go CPU profile from the registered pprof endpoint.",
        "risk_level": "R2",
        "requires_approval": True,
        "required_capabilities": ["go_pprof"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["agent_id", "pid", "duration_seconds", "sample_rate"],
            "properties": {
                "agent_id": {"type": "string", "minLength": 1},
                "pid": {"type": "integer", "minimum": 1},
                "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
                "sample_rate": {"type": "integer", "minimum": 1, "maximum": 999},
            },
        },
    },
    {
        "name": "start_continuous_profile",
        "version": "1.0",
        "description": "Collect several bounded perf windows to compare hotspot drift.",
        "risk_level": "R2",
        "requires_approval": True,
        "required_capabilities": ["continuous_perf"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["agent_id", "pid", "duration_seconds", "sample_rate"],
            "properties": {
                "agent_id": {"type": "string", "minLength": 1},
                "pid": {"type": "integer", "minimum": 1},
                "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
                "sample_rate": {"type": "integer", "minimum": 1, "maximum": 999},
            },
        },
    },
]


TOOL_BY_NAME = {item["name"]: item for item in TOOLS}

# Semantic tool name -> executable TaskKind. Tools without a TaskKind executor
# (for example get_agent_status) intentionally stay out of this mapping.
TOOL_TO_COLLECTOR = {
    "collect_sys_metrics": "sys_metrics",
    "start_perf_profile": "perf_cpu",
    "start_ebpf_io_profile": "ebpf_io",
    "start_pyspy_profile": "pyspy",
    "start_jvm_profile": "java_async",
    "collect_memory_profile": "memory_smaps",
    "collect_go_profile": "go_pprof",
    "start_continuous_profile": "continuous_perf",
    # Reserved for an optional database-aware Agent plugin. The policy denies
    # it unless an Agent explicitly advertises the database_lock capability.
    "collect_database_diagnostics": "database_lock",
}
