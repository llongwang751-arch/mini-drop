import re


def plan_collection(text, agents):
    if not text or not text.strip():
        raise ValueError("natural-language request is required")
    online = [agent for agent in agents if agent["online"]]
    if not online:
        raise ValueError("no online agent is available")
    pid_match = re.search(r"\bpid\s*[:=]?\s*(\d+)\b", text, re.I)
    if not pid_match:
        raise ValueError("request must include a target PID, for example: PID 1234")
    duration = re.search(r"(\d+)\s*(?:秒|s(?:ec(?:onds?)?)?)\b", text, re.I)
    rate = re.search(r"(\d+)\s*hz\b", text, re.I)
    agent_match = re.search(r"agent\s*[:=]?\s*([\w.-]+)", text, re.I)
    lowered = text.lower()
    collector = "pyspy" if any(x in lowered for x in ("py-spy", "pyspy", "python")) else "perf"
    if any(x in lowered for x in ("ebpf", "bpf", "内核", "io", "i/o", "写入")):
        collector = "ebpf"
    agent_id = agent_match.group(1) if agent_match else online[0]["id"]
    if not any(agent["id"] == agent_id and agent["online"] for agent in agents):
        raise ValueError(f"agent {agent_id} is not online")
    return {
        "agent_id": agent_id,
        "pid": int(pid_match.group(1)),
        "duration": int(duration.group(1)) if duration else 5,
        "rate": int(rate.group(1)) if rate else 49,
        "collector": collector,
        "continuous": any(x in lowered for x in ("continuous", "持续", "常驻")),
    }
