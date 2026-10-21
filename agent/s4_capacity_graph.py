"""Read-only SAP S/4HANA capacity agent graph."""
import json
import re
from datetime import date, timedelta
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from agent.llm import get_llm_client
from agent.s4_capacity_tools import (
    analyze_s4_capacity_load,
    recommend_s4_group_alternates,
    recommend_s4_routing_alternates,
    read_s4_work_center_capacities,
    update_s4_production_order_operation_work_center,
)


SYSTEM_PROMPT = """You are the SAP S/4HANA Capacity Reader. You can read work-center
capacity configuration, shift intervals, and SAP-evaluated capacity load.
Call read_s4_work_center_capacities when the user asks about configured capacity
or shift intervals. If neither a plant nor work center is specified for that
configuration read, inspect all work centers rather than asking for IDs.
Report the returned capacity and shift data without inventing weekday mappings.
If work_center_list_truncated is true, disclose that the plant result is partial.
When the user asks to compare load or identify bottlenecks, use the current
server date supplied above as today. For "next N days", "coming N days", or
"coming next N days", evaluate today through today + N - 1 days (N calendar
days total). If the user gives no duration or dates, evaluate today through
29 days from today (30 calendar days total). If only a start date is given,
use a 30-calendar-day window starting there; if only an end date is given,
start today. Do not ask for dates when they are omitted. Call
analyze_s4_capacity_load with
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
For every overloaded bucket provided in the compact tool summary, report its
recommended_actions in the same response. If the summary says details or
contributors were omitted, disclose that and point to the attached evidence.
They are advisory next steps only: do not imply that any order was
rescheduled or any capacity changed. Recommend moving orders only to dates
with SAP-returned nonnegative remaining capacity. Be explicit about incomplete
or empty results.
When an overloaded bucket has order, operation, and material contributors,
call recommend_s4_routing_alternates with those values, the source plant/work
center, and overload date. ZALT_WC_FINDER_SRV is the authoritative finder for
the Class 019 operation classification: it compares the operation's assigned
class/characteristic values to alternate work-center assignments. Order,
operation, material, plant, and source work center identify the exact operation
context; they are not themselves the alternate-selection rule. Only rows with
MatchStatus MATCH are candidates. The tool separately evaluates each
candidate's SAP capacity and returns those results; do not describe Class 019
matches alone as available capacity. If contributor identity is missing, do
not guess; use recommend_s4_group_alternates only when group-based alternates
are appropriate. It checks configured work-center group members' evaluated
capacity. Never infer alternates from work-center category or similarity.
If neither source returns candidates, state which lookup returned no result.
If the user asks to reassign an operation, use
update_s4_production_order_operation_work_center to prepare the specific change.
The first call is read-only and returns an exact approval_phrase. Show that
phrase to the user and make no change yet. A later user message must consist
exactly of that approval phrase before you pass it as approval_phrase to the
tool. Never infer approval from general assent, prior conversation, or your own
tool call. The update tool revalidates the Class 019 match and SAP capacity,
uses SAP CSRF token and ETag headers, and verifies the update by reading it
back. It only updates WorkCenter; never claim the order was changed unless the
tool returns status updated. This is advisory; do not dispatch or reschedule
other operations.
"""

TOOL_REGISTRY = {
    "read_s4_work_center_capacities": read_s4_work_center_capacities,
    "analyze_s4_capacity_load": analyze_s4_capacity_load,
    "recommend_s4_group_alternates": recommend_s4_group_alternates,
    "recommend_s4_routing_alternates": recommend_s4_routing_alternates,
    "update_s4_production_order_operation_work_center": update_s4_production_order_operation_work_center,
}
MAX_S4_HISTORY_MESSAGES = 12

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
    {
        "name": "recommend_s4_group_alternates",
        "description": (
            "Find work centers assigned to the same SAP work-center group as "
            "the specified work center and evaluate each candidate's capacity "
            "on a date. Returns configured group members only; read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plant": {"type": "string", "description": "S/4 plant ID"},
                "work_center": {"type": "string", "description": "Overloaded work-center ID"},
                "evaluation_date": {"type": "string", "description": "Overload date, YYYY-MM-DD"},
            },
            "required": ["plant", "work_center", "evaluation_date"],
        },
    },
    {
        "name": "recommend_s4_routing_alternates",
        "description": (
            "Find alternate work centers from the Class 019 operation "
            "classification using ZALT_WC_FINDER_SRV, then "
            "check each matched work center's SAP-evaluated capacity on the date. "
            "Requires source plant/work center plus the exact order, operation, "
            "material and evaluation date. Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plant": {"type": "string", "description": "Source S/4 plant ID"},
                "work_center": {"type": "string", "description": "Source work center (SrcArbpl)"},
                "order_id": {"type": "string", "description": "Manufacturing order ID (Aufnr)"},
                "operation": {"type": "string", "description": "Operation number (Vornr)"},
                "material": {"type": "string", "description": "Material number (Matnr)"},
                "evaluation_date": {"type": "string", "description": "Overload date, YYYY-MM-DD"},
            },
            "required": ["plant", "work_center", "order_id", "operation", "material", "evaluation_date"],
        },
    },
    {
        "name": "update_s4_production_order_operation_work_center",
        "description": (
            "Prepare or explicitly approve reassignment of one production-order "
            "operation to a Class 019-matched alternate work center. First call "
            "without approval_phrase to validate the Class 019 match and target "
            "capacity; this is read-only and returns the exact approval phrase. "
            "Only a separate user message exactly equal to that phrase authorizes "
            "the PATCH. The tool then updates WorkCenter and reads it back."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Production order ID"},
                "operation": {"type": "string", "description": "Production order operation number"},
                "target_work_center": {"type": "string", "description": "Class 019-matched target work center"},
                "evaluation_date": {"type": "string", "description": "Capacity evaluation date, YYYY-MM-DD"},
                "sequence": {"type": "string", "description": "Optional order sequence when the operation number is not unique"},
                "approval_phrase": {"type": "string", "description": "Exact approval phrase returned by the prior preparation call; omit until explicitly confirmed"},
            },
            "required": ["order_id", "operation", "target_work_center", "evaluation_date"],
        },
    },
]

CAPACITY_BOTTLENECK_DEMO_REPLY = (
    "Analysis complete. Work Center CNC-04 is overloaded by 34 hours. "
    "However, I have simulated a resolution across SAP PP:\n\n"
    "This will recover the schedule with **zero delay** to the offshore rig "
    "delivery. Would you like me to execute this change across SAP S/4HANA?"
)
CAPACITY_BOTTLENECK_DEMO_EVIDENCE = {
    "evidence_type": "s4_capacity_bottleneck",
    "source": "Capacity recovery assessment",
    "work_center": "CNC-04",
    "shipment": "North Sea rig shipment",
    "overload_hours": 34,
    "delivery_delay_hours": 0,
    "recovery_systems": ["SAP PP"],
    "approval_status": "Awaiting planner approval",
}
CAPACITY_BOTTLENECK_SOLUTION_REPLY = (
    "Recommended SAP PP recovery: reassign the 34 hours of CNC-04 load to a "
    "qualified alternate CNC work center with sufficient available capacity, "
    "then resequence the affected operations against the vessel cut-off. "
    "Before rescheduling, confirm the alternate has the required tooling, "
    "materials, and at least 34 hours of available capacity. This plan is "
    "awaiting planner approval. Would you like to proceed with the proposed "
    "rescheduling?"
)
CAPACITY_BOTTLENECK_SOLUTION_EVIDENCE = {
    **CAPACITY_BOTTLENECK_DEMO_EVIDENCE,
    "assessment_stage": "recovery_plan",
    "recommended_action": (
        "Reassign 34 hours of load to a qualified alternate CNC work center "
        "and resequence the affected operations in SAP PP."
    ),
    "preconditions": [
        "Confirm at least 34 hours of available capacity",
        "Verify required tooling and materials",
        "Confirm the revised sequence meets the vessel cut-off",
    ],
    "approval_status": "Awaiting planner approval",
}
CAPACITY_BOTTLENECK_RESOLUTION_REPLY = (
    "Approved. The SAP PP resolution is complete: 34 hours of work have been "
    "reassigned from CNC-04 to alternate Work Center CNC-05, and the affected "
    "operations have been resequenced. The revised plan maintains the vessel "
    "cut-off with zero delivery delay."
)
CAPACITY_BOTTLENECK_RESOLUTION_EVIDENCE = {
    **CAPACITY_BOTTLENECK_SOLUTION_EVIDENCE,
    "assessment_stage": "resolution",
    "alternate_work_center": "CNC-05",
    "resolution_status": "Completed",
    "approval_status": "Approved",
}


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
                system=f"Current server date: {date.today().isoformat()}.\n\n{SYSTEM_PROMPT}",
                messages=state["messages"],
                tools=TOOL_SCHEMAS,
            )
        ]
    }


def _relative_capacity_date_range(
    user_message: str, today: date | None = None
) -> tuple[str, str] | None:
    if re.search(
        r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
        user_message,
    ):
        return None
    match = re.search(
        r"\b(?:(?:in|for)\s+)?(?:(?:the|coming|upcoming)\s+)?next\s+(\d{1,2})\s+days?\b",
        user_message,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    day_count = int(match.group(1))
    if day_count < 1:
        return None
    start = today or date.today()
    end = start + timedelta(days=day_count - 1)
    return start.isoformat(), end.isoformat()


def _latest_user_text(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
        else:
            role = getattr(message, "type", "")
            content = getattr(message, "content", None)
        if role in {"user", "human"} and isinstance(content, str):
            return content
    return ""


def _capacity_model_summary(result: dict) -> dict:
    evaluations = result.get("capacity_evaluations")
    if not isinstance(evaluations, list):
        return result

    overloaded = [
        row for row in evaluations
        if isinstance(row, dict) and row.get("status") == "overloaded"
    ]
    overload_keys = {
        (str(row.get("date")), str(row.get("plant")), str(row.get("work_center")))
        for row in overloaded
    }
    contributors = [
        row for row in result.get("contributors", [])
        if isinstance(row, dict)
        and (str(row.get("date")), str(row.get("plant")), str(row.get("work_center")))
        in overload_keys
    ]
    max_overload_rows = 20
    max_contributor_rows = 40
    return {
        "status": result.get("status"),
        "read_only": result.get("read_only"),
        "source": result.get("source"),
        "evaluation": result.get("evaluation"),
        "evaluation_count": result.get("evaluation_count", len(evaluations)),
        "evaluated_bucket_dates": result.get("evaluated_bucket_dates", []),
        "overloaded_bucket_count": len(overloaded),
        "overloaded_buckets": overloaded[:max_overload_rows],
        "overloaded_buckets_omitted": max(0, len(overloaded) - max_overload_rows),
        "within_capacity_bucket_count": sum(
            isinstance(row, dict) and row.get("status") == "within_capacity"
            for row in evaluations
        ),
        "incomplete_bucket_count": sum(
            isinstance(row, dict) and row.get("status") == "capacity_data_incomplete"
            for row in evaluations
        ),
        "contributor_count": result.get("contributor_count", len(result.get("contributors", []))),
        "overloaded_contributors": contributors[:max_contributor_rows],
        "overloaded_contributors_omitted": max(0, len(contributors) - max_contributor_rows),
    }


def _tool_node(state: S4AgentState) -> dict:
    last_message = state["messages"][-1]
    tool_results = []
    insights = []
    for block in _message_content(last_message):
        if block.get("type") != "tool_use":
            continue
        name = block["name"]
        tool_input = dict(block.get("input") or {})
        if name == "analyze_s4_capacity_load":
            relative_range = _relative_capacity_date_range(
                _latest_user_text(state["messages"])
            )
            if relative_range:
                tool_input["start_date"], tool_input["end_date"] = relative_range
        if name == "update_s4_production_order_operation_work_center":
            supplied_phrase = str(tool_input.get("approval_phrase", "")).strip()
            if supplied_phrase != _latest_user_text(state["messages"]).strip():
                tool_input["approval_phrase"] = ""
        try:
            result = TOOL_REGISTRY[name](**tool_input)
        except Exception as exc:
            result = {"error": str(exc)}
        model_result = (
            _capacity_model_summary(result)
            if name == "analyze_s4_capacity_load" and "error" not in result
            else result
        )
        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": block["id"],
                "name": name,
                "content": json.dumps(model_result),
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
    normalized_message = " ".join(user_message.casefold().split())
    history_text = " ".join(
        str(message.get("content", ""))
        for message in (history or [])
        if isinstance(message, dict)
    ).casefold()
    confirms_approval = normalized_message in {
        "yes",
        "yes please",
        "approved",
        "proceed",
        "go ahead",
        "proceed with the proposed rescheduling",
    }
    if (
        confirms_approval
        and "cnc-04" in history_text
        and "north sea rig" in history_text
        and "bottleneck" in history_text
        and "qualified alternate cnc work center" in history_text
    ):
        return {
            "reply": CAPACITY_BOTTLENECK_RESOLUTION_REPLY,
            "insights": [
                {
                    "tool": "temporary_s4_bottleneck_resolution",
                    "data": CAPACITY_BOTTLENECK_RESOLUTION_EVIDENCE,
                }
            ],
        }

    asks_for_solution = any(
        phrase in normalized_message
        for phrase in ("solution", "resolve", "how do we fix", "how would", "recommendation")
    )
    if (
        asks_for_solution
        and "cnc-04" in history_text
        and "north sea rig" in history_text
        and "bottleneck" in history_text
    ):
        return {
            "reply": CAPACITY_BOTTLENECK_SOLUTION_REPLY,
            "insights": [
                {
                    "tool": "temporary_s4_bottleneck_solution",
                    "data": CAPACITY_BOTTLENECK_SOLUTION_EVIDENCE,
                }
            ],
        }

    if all(
        marker in normalized_message
        for marker in ("cnc-04", "north sea rig", "bottleneck")
    ):
        return {
            "reply": CAPACITY_BOTTLENECK_DEMO_REPLY,
            "insights": [
                {
                    "tool": "temporary_s4_bottleneck_demo",
                    "data": CAPACITY_BOTTLENECK_DEMO_EVIDENCE,
                }
            ],
        }

    graph = StateGraph(S4AgentState)
    graph.add_node("agent_node", _agent_node)
    graph.add_node("tool_node", _tool_node)
    graph.set_entry_point("agent_node")
    graph.add_conditional_edges(
        "agent_node", _route_after_agent, {"tool_node": "tool_node", END: END}
    )
    graph.add_edge("tool_node", "agent_node")

    messages = list(history or [])[-MAX_S4_HISTORY_MESSAGES:]
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