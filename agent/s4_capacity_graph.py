"""Read-only SAP S/4HANA capacity agent graph."""
import json
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from agent.llm import get_llm_client
from agent.s4_capacity_tools import (
    analyze_s4_capacity_load,
    read_s4_work_center_capacities,
)


SYSTEM_PROMPT = """You are the SAP S/4HANA Capacity Reader. You can read work-center
capacity configuration, weekday shift intervals, and production-order operation
standard-work hours. Always call
read_s4_work_center_capacities before reporting capacity values. If neither a
plant nor work center is specified, ask the user to provide one. Report the
configured capacity, time window, breaks, factory calendar, validity, and shift
hours returned by the tool. Explain weekday codes as codes unless their mapping
is provided. If work_center_list_truncated is true, disclose that the plant
result is partial. Do not analyze production-order load, diagnose bottlenecks, suggest
capacity changes, reschedule orders, or write to SAP. Be explicit when the
service returns no records or incomplete capacity details. When the user asks
to compare work-center load, call analyze_s4_capacity_load. Report standard
hours grouped by scheduled start date with operation/order counts, and explain
that the full operation hours are assigned to that start-date bucket rather
than apportioned across a multi-day window. These hours are the values reported
by the S/4 operation standard-work fields; do not derive new hours from order
quantity. The tool does not determine overload status because weekday mapping
and factory calendar applicability are not yet validated. Do not claim that the
work center is overloaded or within capacity based on this comparison alone.
"""

TOOL_REGISTRY = {
    "read_s4_work_center_capacities": read_s4_work_center_capacities,
    "analyze_s4_capacity_load": analyze_s4_capacity_load,
}

TOOL_SCHEMAS = [
    {
        "name": "read_s4_work_center_capacities",
        "description": (
            "Read SAP S/4HANA work-center capacity configuration and associated "
            "weekday shift intervals. Provide a plant, work center, or both. "
            "This tool is read-only and does not calculate order load or bottlenecks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plant": {"type": "string", "description": "Optional S/4 plant ID"},
                "work_center": {
                    "type": "string",
                    "description": "Optional work-center/resource ID",
                },
            },
            "required": [],
        },
    },
    {
        "name": "analyze_s4_capacity_load",
        "description": (
            "Read non-deleted production-order operations for a required plant "
            "and work center, sum their reported standard-work hours by "
            "scheduled start date, and include configured shifts as context. "
            "Read-only. Does not determine overload status because weekday and "
            "factory-calendar semantics are not yet validated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plant": {"type": "string", "description": "S/4 plant ID"},
                "work_center": {"type": "string", "description": "S/4 work-center ID"},
            },
            "required": ["plant", "work_center"],
        },
    },
]


class S4AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    insights: list[dict]


def _message_content(message) -> list:
    return message.content if hasattr(message, "content") else message.get("content", [])


def _agent_node(state: S4AgentState) -> dict:
    client = get_llm_client()
    return {
        "messages": [
            client.chat(
                system=SYSTEM_PROMPT,
                messages=state["messages"],
                tools=TOOL_SCHEMAS,
            )
        ]
    }


def _tool_node(state: S4AgentState) -> dict:
    last_message = state["messages"][-1]
    tool_results = []
    insights = []
    for block in _message_content(last_message):
        if block.get("type") != "tool_use":
            continue
        name = block["name"]
        try:
            result = TOOL_REGISTRY[name](**block["input"])
        except Exception as exc:
            result = {"error": str(exc)}
        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": block["id"],
                "name": name,
                "content": json.dumps(result),
            }
        )
        insights.append({"tool": name, "data": result})
    return {
        "messages": [{"role": "user", "content": tool_results}],
        "insights": state.get("insights", []) + insights,
    }


def _route_after_agent(state: S4AgentState) -> str:
    has_tool_call = any(
        block.get("type") == "tool_use"
        for block in _message_content(state["messages"][-1])
    )
    return "tool_node" if has_tool_call else END


def run_s4_agent_with_data(user_message: str, history: list[dict] | None = None) -> dict:
    graph = StateGraph(S4AgentState)
    graph.add_node("agent_node", _agent_node)
    graph.add_node("tool_node", _tool_node)
    graph.set_entry_point("agent_node")
    graph.add_conditional_edges(
        "agent_node", _route_after_agent, {"tool_node": "tool_node", END: END}
    )
    graph.add_edge("tool_node", "agent_node")

    messages = list(history or [])
    messages.append({"role": "user", "content": user_message})
    final_state = graph.compile().invoke({"messages": messages, "insights": []})
    last_message = final_state["messages"][-1]
    text_blocks = [
        block["text"]
        for block in _message_content(last_message)
        if block.get("type") == "text"
    ]
    return {
        "reply": "\n".join(text_blocks) if text_blocks else str(last_message),
        "insights": final_state.get("insights", []),
    }