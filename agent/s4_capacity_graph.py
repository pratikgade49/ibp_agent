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
capacity configuration, shift intervals, and SAP-evaluated capacity load.
Call read_s4_work_center_capacities when the user asks about configured capacity
or shift intervals. If neither a plant nor work center is specified for that
configuration read, inspect all work centers rather than asking for IDs.
Report the returned capacity and shift data without inventing weekday mappings.
If work_center_list_truncated is true, disclose that the plant result is partial.
When the user asks to compare load or identify bottlenecks, date inputs are
optional. If both dates are omitted, evaluate today through 29 days from today
(30 calendar days total). If only a start date is given, use a 30-calendar-day
window starting there; if only an end date is given, start today. Do not ask
for dates when they are omitted. Call analyze_s4_capacity_load with
bucket_type='D' for daily evaluation. Plant and work-center filters are
optional: when the user asks across all locations/work centers, omit both
filters so SAP evaluates all accessible combinations. Do not ask the user to
provide IDs for an all-locations request. A load-only analysis does not need a
configuration read. This tool reads SAP's evaluated
capacity bucket and its order contributors. Treat WorkCenterCapRqmtInCapUnit,
WorkCenterAvailableCapacity, WrkCtrRmngCapInCapUnit, and
WorkCenterTotUtilznInTmePerd from SAP as authoritative. Report negative
remaining capacity as overloaded according to SAP's evaluation; do not
recalculate aggregate demand from operation standard-work fields or sum
contributors in place of the aggregate bucket. List contributor orders and
their SAP-evaluated capacity requirements. If no evaluation rows are returned,
say that SAP returned no evaluated buckets; do not infer from standard values.
For date ranges, report the returned evaluated-bucket count and dates. Only
describe a date as within capacity when SAP returned a bucket for that date
with nonnegative remaining capacity. Do not claim unreturned dates were within
capacity; state that no bucket was returned for them and their status is
unverified. A single returned bucket does not establish coverage of the full
requested date range.
This agent is read-only: do not suggest executed capacity changes, reschedule
orders, or write to SAP. Be explicit about incomplete or empty results.
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
            "Read SAP's parameterized work-center capacity evaluation and "
            "per-order load distribution for a date range and bucket type, "
            "optionally filtered by plant and/or work center. Omit both filters "
            "to evaluate all accessible plants and work centers. Returns evaluated required capacity, "
            "available capacity, remaining capacity, utilization, and order "
            "contributors. Use bucket_type D for daily evaluation. Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plant": {"type": "string", "description": "Optional S/4 plant filter; omit for all plants"},
                "work_center": {"type": "string", "description": "Optional work-center filter; omit for all work centers"},
                "start_date": {"type": "string", "description": "Optional evaluation start date, YYYY-MM-DD; defaults to today"},
                "end_date": {"type": "string", "description": "Optional evaluation end date, YYYY-MM-DD; defaults to 30 calendar days after the start"},
                "bucket_type": {"type": "string", "description": "One-character SAP bucket code; use D for daily"},
            },
            "required": [],
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