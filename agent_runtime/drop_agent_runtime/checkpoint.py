"""只保存可见消息的 AgentScope 检查点投影；权限、计数和副作用回执始终由 Go 持有。"""

from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.state import AgentState

from .wire import ProtocolError, json_bytes, strict_json


def parse_checkpoint(value, budget):
    required = {
        "sequence",
        "model_calls",
        "tool_calls",
        "round_tools",
        "format_only",
        "state",
        "usage",
        "completed_call_ids",
    }
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or set(value) - required - {"accepted_json"}
    ):
        raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
    for key, maximum in (
        ("sequence", 1000),
        ("model_calls", budget["max_model_calls"]),
        ("tool_calls", budget["max_tool_calls"]),
        ("round_tools", budget["max_tools_per_round"]),
    ):
        if type(value[key]) is not int or not 0 <= value[key] <= maximum:
            raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
    if type(value["format_only"]) is not bool or not isinstance(value["usage"], dict):
        raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
    for key, count in value["usage"].items():
        if (
            key not in {"input_tokens", "output_tokens"}
            or type(count) is not int
            or not 0 <= count <= 1000000000
        ):
            raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
    ids = value["completed_call_ids"]
    if (
        not isinstance(ids, list)
        or len(ids) > budget["max_tool_calls"]
        or any(not isinstance(item, str) or not 1 <= len(item) <= 128 for item in ids)
        or len(ids) != len(set(ids))
    ):
        raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
    if "accepted_json" in value:
        accepted = value["accepted_json"]
        if not isinstance(accepted, str) or len(accepted.encode()) > 65536:
            raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
        strict_json(accepted)
    state = value["state"]
    if state == {}:
        if (
            value["sequence"] != 0
            or value["model_calls"] != 0
            or value["tool_calls"] != 0
        ):
            raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
        return value
    validate_state(state)
    return value


def validate_state(state):
    if (
        not isinstance(state, dict)
        or set(state) != {"context", "cursor", "pending_text", "seen_ids"}
        or len(json_bytes(state)) > 131072
    ):
        raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
    if (
        state["cursor"] not in {"model", "tools", "finish"}
        or not isinstance(state["pending_text"], str)
        or len(state["pending_text"].encode()) > 49152
    ):
        raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
    if (
        not isinstance(state["seen_ids"], list)
        or len(state["seen_ids"]) > 4096
        or any(not isinstance(i, str) or len(i) > 128 for i in state["seen_ids"])
    ):
        raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
    if not isinstance(state["context"], list) or len(state["context"]) > 64:
        raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
    allowed_blocks = {
        "text": {"type", "text"},
        "tool_call": {"type", "id", "name", "input", "state"},
        "tool_result": {"type", "id", "name", "output", "state"},
    }
    for message in state["context"]:
        if (
            not isinstance(message, dict)
            or set(message) != {"id", "name", "role", "content"}
            or message["role"] not in {"user", "assistant"}
            or not isinstance(message["content"], list)
            or len(message["content"]) > 256
        ):
            raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
        for block in message["content"]:
            if (
                not isinstance(block, dict)
                or block.get("type") not in allowed_blocks
                or set(block) != allowed_blocks[block["type"]]
            ):
                raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
            if message["role"] == "user" and block["type"] != "text":
                raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
            if block["type"] == "tool_result" and (
                not isinstance(block["output"], list)
                or any(
                    not isinstance(item, dict)
                    or set(item) != {"type", "text"}
                    or item["type"] != "text"
                    for item in block["output"]
                )
            ):
                raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
        Msg.model_validate(message)


def restored_state(checkpoint):
    state = checkpoint.get("state", {}) if checkpoint else {}
    if not state:
        return AgentState()
    return AgentState(
        context=[Msg.model_validate(message) for message in state["context"]]
    )


def project_state(
    agent, session, cursor="model", pending_text="", append=None, tool_result=None
):
    """投影公开 AgentState，刻意丢弃 usage/metadata/权限缓存等 SDK 内部字段。"""
    state = agent.state.model_copy(deep=True)
    if append:
        state.append_context(
            agent.name, [item.model_copy(deep=True) for item in append]
        )
    if tool_result is not None:
        state.append_context(agent.name, [tool_result])
        for message in state.context:
            for block in message.content:
                if isinstance(block, ToolCallBlock) and block.id == tool_result.id:
                    block.state = "finished"
    context = []
    for message in state.context:
        blocks = []
        for block in message.content:
            if isinstance(block, TextBlock):
                blocks.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolCallBlock):
                blocks.append(
                    {
                        "type": "tool_call",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                        "state": str(block.state),
                    }
                )
            elif isinstance(block, ToolResultBlock):
                output = (
                    block.output
                    if isinstance(block.output, list)
                    else [TextBlock(text=block.output)]
                )
                if any(not isinstance(item, TextBlock) for item in output):
                    raise ProtocolError("AGENT_CHECKPOINT_REJECTED")
                blocks.append(
                    {
                        "type": "tool_result",
                        "id": block.id,
                        "name": block.name,
                        "output": [
                            {"type": "text", "text": item.text} for item in output
                        ],
                        "state": str(block.state),
                    }
                )
        context.append(
            {
                "id": message.id,
                "name": message.name,
                "role": message.role,
                "content": blocks,
            }
        )
    result = {
        "context": context,
        "cursor": cursor,
        "pending_text": pending_text,
        "seen_ids": sorted(session.seen_ids),
    }
    validate_state(result)
    return result
