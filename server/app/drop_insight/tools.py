"""Allow-listed tools visible to the Drop Insight planner."""

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
]
