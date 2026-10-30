"""Read-only SAP S/4HANA maintenance schedule agent graph."""
import json
from datetime import date
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from agent.llm import get_llm_client
from agent.maintenance_tools import read_s4_maintenance_schedule


SYSTEM_PROMPT = """You are the SAP S/4HANA Maintenance Schedule Agent. Read planned
maintenance calls and identify their linked production work center, plant,
maintenance item, and maintenance order. Use read_s4_maintenance_schedule for
maintenance schedule questions. Respect the user's date and work-center filters;
when dates are omitted, use the tool's default upcoming 30-calendar-day window.
Report the scheduled dates and call status returned by SAP. Prefer the order's
scheduled start/end interval; use the plan's next planned date when no order
schedule is available. Explain missing links as unverified, never infer a work
center from descriptions or the maintenance crew work center. The returned
maintenance interval is evidence of planned work, not proof of capacity loss.
Do not estimate or subtract capacity; capacity evaluation belongs to the S/4
Capacity agent. This agent is read-only and must never create, change, release,
or schedule maintenance orders. State clearly when no calls are returned or
when SAP data is incomplete. Use concise planner-friendly language and a compact
table for multiple calls."""

TOOL_SCHEMAS = [{
    "name": "read_s4_maintenance_schedule",
    "description": (
        "Read scheduled S/4 maintenance calls and link them to maintenance "
        "items' production work centers. Read-only. Dates default to the next "
        "30 calendar days."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "Optional start date, YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "Optional end date, YYYY-MM-DD"},
            "plant": {"type": "string", "description": "Optional production plant filter"},
            "work_center": {"type": "string", "description": "Optional production work-center filter"},
        },
        "required": [],
    },
}]
TOOL_REGISTRY = {"read_s4_maintenance_schedule": read_s4_maintenance_schedule}
MAX_HISTORY_MESSAGES = 12
MAX_GRAPH_STEPS = 8


class MaintenanceAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    insights: list[dict]


def _message_content(message) -> list:
    return message.content if hasattr(message, "content") else message.get("content", [])


def _agent_node(state: MaintenanceAgentState) -> dict:
    client = get_llm_client()
    return {"messages": [client.chat(
        system=f"Current server date: {date.today().isoformat()}.\n\n{SYSTEM_PROMPT}",
        messages=state["messages"],
        tools=TOOL_SCHEMAS,
        max_tokens=3072,
    )]}


def _tool_node(state: MaintenanceAgentState) -> dict:
    last_message = state["messages"][-1]
    tool_results = []
    insights = []
    for block in _message_content(last_message):
        if block.get("type") != "tool_use":
            continue
        name = block["name"]
        try:
            result = TOOL_REGISTRY[name](**dict(block.get("input") or {}))
        except Exception as exc:
            result = {"error": str(exc)[:1500]}
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block["id"],
            "name": name,
            "content": json.dumps(result),
        })
        insights.append({"tool": name, "data": result})
    return {
        "messages": [{"role": "user", "content": tool_results}],
        "insights": state.get("insights", []) + insights,
    }


def _route_after_agent(state: MaintenanceAgentState) -> str:
    has_tool_call = any(
        block.get("type") == "tool_use"
        for block in _message_content(state["messages"][-1])
    )
    return "tool_node" if has_tool_call else END


def _build_graph():
    graph = StateGraph(MaintenanceAgentState)
    graph.add_node("agent_node", _agent_node)
    graph.add_node("tool_node", _tool_node)
    graph.set_entry_point("agent_node")
    graph.add_conditional_edges(
        "agent_node", _route_after_agent, {"tool_node": "tool_node", END: END}
    )
    graph.add_edge("tool_node", "agent_node")
    return graph.compile()


_COMPILED_GRAPH = _build_graph()


def run_maintenance_agent_with_data(
    user_message: str, history: list[dict] | None = None
) -> dict:
    messages = list(history or [])[-MAX_HISTORY_MESSAGES:]
    messages.append({"role": "user", "content": user_message})
    final_state = _COMPILED_GRAPH.invoke(
        {"messages": messages, "insights": []},
        config={"recursion_limit": MAX_GRAPH_STEPS},
    )
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