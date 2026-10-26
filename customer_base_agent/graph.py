"""LangGraph orchestration for the isolated customer-base forecast agent."""

from __future__ import annotations

import json
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from agent.llm import get_llm_client
from customer_base_agent.service import commit_forecast, prepare_forecast


SYSTEM_PROMPT = """You are the Customer Base Forecast Agent. You orchestrate a deterministic
calculation service and must never calculate forecast numbers yourself.
Use prepare_customer_base_forecast for a planner's request to calculate a
customer-base forecast. Require the parent planning version (INSPROD.VERSIONID)
and scenario ID (INSPROD.SCENARIOID), plus the period range. For Baseline, pass
the literal string BASELINE; the service maps this to an empty VERSIONID.
Read the month immediately before start_period as the numeric seed from
ZINSCUSTOMERBASEFCSTTYPE. Calculate every requested month, including the
first, from the prior month's forecast and that month's inputs. Show the returned forecast preview and
warnings. If a country is specified, pass it through as the country filter.
Do not submit a write during preparation.
Only call commit_customer_base_forecast if the latest user message is exactly
the approval_phrase returned by preparation. The approval phrase identifies
the prepared run; do not invent or substitute a run ID. General agreement such as yes,
approve, or proceed is not sufficient. Report import_submitted as submitted,
not completed; the connector does not yet poll IBP processing or reconcile the
read-back. Never claim scenario access has been verified unless input rows were
returned for the requested version."""

TOOL_SCHEMAS = [
    {
        "name": "prepare_customer_base_forecast",
        "description": "Validate access by reading scenario inputs, calculate the deterministic customer-base forecast, and return a preview requiring exact approval before write-back.",
        "input_schema": {
            "type": "object",
            "properties": {
                "planning_version_id": {"type": "string", "description": "Parent planning version ID from INSPROD.VERSIONID; pass BASELINE for the baseline version"},
                "scenario_id": {"type": "string", "description": "Scenario ID from INSPROD.SCENARIOID"},
                "start_period": {"type": "string", "description": "First month to calculate, YYYY-MM; calculation uses the preceding month's forecast as its seed"},
                "end_period": {"type": "string", "description": "Last output month, YYYY-MM"},
                "country": {"type": "string", "description": "Optional country code to limit input reads, calculation, preview, and import"},
            },
            "required": ["planning_version_id", "scenario_id", "start_period", "end_period"],
        },
    },
    {
        "name": "commit_customer_base_forecast",
        "description": "Submit the prepared forecast identified by the exact approval phrase, only after that phrase is the latest user message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "approval_phrase": {"type": "string"},
            },
            "required": ["approval_phrase"],
        },
    },
]
TOOL_REGISTRY = {
    "prepare_customer_base_forecast": prepare_forecast,
    "commit_customer_base_forecast": commit_forecast,
}


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    insights: list[dict]
    requester: str


def _content(message) -> list:
    return message.content if hasattr(message, "content") else message.get("content", [])


def _latest_user_text(messages: list) -> str:
    for message in reversed(messages):
        role = message.get("role") if isinstance(message, dict) else getattr(message, "type", "")
        text = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if role in {"user", "human"} and isinstance(text, str):
            return text
    return ""


def _tool_result_for_model(name: str, result: dict) -> dict:
    if name != "prepare_customer_base_forecast" or result.get("status") != "prepared":
        return result
    summary = {
        key: result[key]
        for key in (
            "status",
            "planning_version_id",
            "scenario_id",
            "country",
            "row_count",
            "approval_phrase",
            "note",
        )
        if key in result
    }
    warnings = result.get("warnings", [])
    if warnings:
        summary["warning_count"] = len(warnings)
        summary["warnings"] = warnings[:10]
    return summary


def _agent_node(state: AgentState) -> dict:
    message = get_llm_client().chat(
        system=SYSTEM_PROMPT,
        messages=state["messages"],
        tools=TOOL_SCHEMAS,
        max_tokens=4096,
    )
    return {"messages": [message]}


def _tool_node(state: AgentState) -> dict:
    message = state["messages"][-1]
    latest_user = _latest_user_text(state["messages"])
    results = []
    insights = []
    replies = []
    for block in _content(message):
        if block.get("type") != "tool_use":
            continue
        name = block["name"]
        arguments = dict(block.get("input") or {})
        arguments["requester"] = state["requester"]
        if name == "commit_customer_base_forecast":
            arguments["user_message"] = latest_user
            if arguments.get("approval_phrase", "").strip() != latest_user.strip():
                result = {"status": "approval_required", "message": "The latest user message must exactly equal the approval phrase."}
            else:
                result = _invoke(name, arguments)
        else:
            result = _invoke(name, arguments)
        model_result = _tool_result_for_model(name, result)
        results.append({
            "type": "tool_result",
            "tool_use_id": block["id"],
            "name": name,
            "content": json.dumps(model_result, default=str),
        })
        insights.append({"tool": name, "data": result})
        if name == "prepare_customer_base_forecast" and result.get("status") == "prepared":
            country_label = f" for country {result['country']}" if result.get("country") else ""
            replies.append(
                f"Forecast prepared for {result['row_count']:,} period rows{country_label} "
                f"in scenario {result['scenario_id']}. Review the preview, then approve with the exact "
                f"phrase: {result['approval_phrase']}. No IBP write has been submitted."
            )
        elif name == "commit_customer_base_forecast" and result.get("status") == "import_submitted":
            replies.append(
                f"The forecast import was submitted for scenario {result['scenario_id']}. "
                "IBP completion and read-back have not yet been verified."
            )
        elif result.get("status") == "approval_required":
            replies.append(result["message"])
        elif result.get("status") == "error":
            replies.append(f"The request could not be completed: {result.get('error', 'Unknown error')}")
        else:
            replies.append(str(result.get("message") or result.get("note") or result.get("status")))
    assistant_reply = "\n".join(replies) or "The request did not produce a tool result."
    return {
        "messages": [
            {"role": "user", "content": results},
            {"role": "assistant", "content": [{"type": "text", "text": assistant_reply}]},
        ],
        "insights": state.get("insights", []) + insights,
    }


def _invoke(name: str, arguments: dict) -> dict:
    try:
        return TOOL_REGISTRY[name](**arguments)
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:1000]}


def _route(state: AgentState) -> str:
    return "tools" if any(block.get("type") == "tool_use" for block in _content(state["messages"][-1])) else END


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("agent", _agent_node)
    graph.add_node("tools", _tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", _route, {"tools": "tools", END: END})
    graph.add_edge("tools", END)
    return graph.compile()


def run_customer_base_agent(
    user_message: str,
    *,
    requester: str,
    history: list[dict] | None = None,
) -> dict:
    if not requester.strip():
        raise ValueError("requester must be supplied from authenticated application context")
    messages = list(history or [])[-6:]
    bounded_history = []
    for item in messages:
        content = item.get("content")
        if isinstance(content, str) and len(content) > 4000:
            item = {**item, "content": content[-4000:]}
        elif isinstance(content, list):
            bounded_blocks = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    bounded_blocks.append({**block, "text": block["text"][-4000:]})
                else:
                    bounded_blocks.append(block)
            item = {**item, "content": bounded_blocks}
        bounded_history.append(item)
    messages = bounded_history
    messages.append({"role": "user", "content": user_message})
    final_state = build_graph().invoke(
        {"messages": messages, "insights": [], "requester": requester}
    )
    last_message = final_state["messages"][-1]
    text_blocks = [block["text"] for block in _content(last_message) if block.get("type") == "text"]
    return {"reply": "\n".join(text_blocks), "insights": final_state.get("insights", [])}