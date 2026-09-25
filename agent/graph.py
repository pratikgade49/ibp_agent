"""
LangGraph state graph for the IBP Demand Planning Agent.
Design: a single ReAct-style loop -- the LLM node decides whether to call a
tool or respond with a final answer; a tool-execution node runs whichever
tool was requested and feeds the result back. This mirrors how the original
Joule Studio skills let the agent "decide on its own how to use them and in
which sequence" (per the design doc), just made explicit and inspectable
instead of hidden behind low-code configuration.
   START -> agent_node -> (tool call?) -> tool_node -> agent_node -> ... -> END
                       \-> (final answer) -----------------------------> END
"""
import json
from typing import Annotated, TypedDict
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from agent.llm import get_llm_client
from agent.tools import (
    analyze_capacity_bottlenecks,
    recommend_capacity_action,
    update_capacity_supply,
   detect_forecast_anomalies,
   get_forecast_vs_consumption,
   get_sales_history_status,
    query_planning_data,
   send_email,
    update_planning_data,
    recommend_planning_action,
    import_master_data,
)
SYSTEM_PROMPT = """You are the IBP Demand Planning Agent. You help demand \
planners validate data, monitor consumption variance, and detect forecast \
anomalies in SAP Integrated Business Planning.
You have ten tools available. Decide which tool(s) to call and in what \
order based on the planner's request -- do not guess numbers yourself, \
always call the relevant tool to get real data first.
For capacity questions, use analyze_capacity_bottlenecks. Match natural-language
resource terms such as production, storage, handling, transport, or all to the
resource_type argument. The tool uses IBP key figures CAPADEMAND and CAPAUSAGE
for handling/storage, PCAPADEMAND and PCAPAUSAGE for production,
and TCAPADEMAND and TCAPAUSAGE for transportation, with CAPASUPPLY as the
capacity-supply key figure.
Use CAPACONSUMPTION for handling/storage, PCAPACONSUMPTION for production,
and TCAPACONSUMPTION for transportation when explaining the capacity demand
rate. Handling and storage are resource-location-product analyses; production
is resource-location-product-source; transportation is product-location-
ship-from-location-mode-of-transport-resource. For transportation capacity
supply, IBP requires the location ID TransResLoc. Production and transportation
are hard constraints in the time-series optimizer; storage is pseudo-hard, and
handling/storage support differs by planning algorithm. Do not claim that a
resource is supported by an algorithm unless the returned configuration or
user-provided context confirms it.
Capacity questions may be phrased as: "Which resources are bottlenecks in the
next three periods?", "Show production capacity shortages", "Which warehouses
are above 80 percent utilization?", or "Is handling capacity sufficient?"
Use period_start_rel=1 and period_end_rel=3 for the next three periods when the
user says next three periods. Use utilization_threshold_pct when the user gives
a percentage.
Report utilization, period, resource, location, product, and production source
when present. Use the IBP formula Capacity Usage / Capacity Supply * 100 =
UTILIZATIONPCT, and set status to High_Utilization when UTILIZATIONPCT > 100,
otherwise Within_Capacity. Do not invent resource IDs or capacity values. If
analysis_status is no_activity_data, explain that all returned demand and usage
values are zero and do not claim that capacity is confirmed within limits. If
analysis_status is incomplete_data, report the warning and do not present the
result as a complete bottleneck assessment. This analysis is read-only; call
recommend_capacity_action for a resolution proposal, and never write capacity
changes without a separate explicit confirmation workflow. To change SUPPLY,
call update_capacity_supply only after the user confirms the exact resource,
location, period, resource type, and new supply value. For transportation, the
location must be TransResLoc.
Use query_planning_data for generic questions involving totals, rankings, \
maximums, minimums, averages, comparisons, or base planning-level results. \
For base planning level, group by product, location, customer, and period \
unless the planner specifies a different grouping. Do not include UOM as a \
query argument; the configured data-source UOM is used.
When a check reveals a problem (variance over threshold, anomalies found, \
or missing sales data), offer to send an email notification via the \
send_email tool, but only send it if the user confirms.
For get_sales_history_status, report readiness and continuity separately: \
ready=true means the target period has ACTUALSQTY data; \
inconsistency_detected=true is a warning about missing historical periods and \
must not be reported as not ready unless ready=false.
For aggregated readiness requests, call get_sales_history_status with \
result_scope='product', 'location', or 'customer' as appropriate. These scopes \
aggregate ACTUALSQTY across the other dimensions. Use result_scope='combination' \
only when the planner wants the exact product/location/customer intersection checked.
If ready=false but last_loaded_period and available_periods contain history, \
report that historical data exists and only the target period is missing. Do not \
say that no historical data exists. Treat target_period_missing separately from \
inconsistency_detected, which is only for gaps between loaded historical periods.
When a tool returns multiple results, report the summary counts and list every
item in alert_results in a compact markdown table with period, product, location,
customer, forecast, actual, variance, and direction. Do not report only the
total count. The tool payload contains alert_results only, not every evaluated
row."""
SYSTEM_PROMPT += """
For update_planning_data, never write on the first request. First explain the
exact product, location, period, version, and new forecast value and ask for explicit
confirmation. Call it with confirm=true only after the user clearly confirms
the proposed change.
When an anomaly or over/under-consumption finding is detected, call
recommend_planning_action and report its summary and actions. Treat its
proposed_forecast as a proposal only; call update_planning_data only after
the user explicitly confirms the exact change.
For update results, report that the import was submitted when the POST
succeeds. Do not call it successfully committed, because this integration
does not poll asynchronous SAP processing status.
For import_master_data, always summarize the master data type, attributes,
record count, and whether this is an import or deletion. Call it only after
the user explicitly confirms the exact operation.
"""
TOOL_REGISTRY = {
    "analyze_capacity_bottlenecks": analyze_capacity_bottlenecks,
    "recommend_capacity_action": recommend_capacity_action,
    "update_capacity_supply": update_capacity_supply,
    "get_forecast_vs_consumption": get_forecast_vs_consumption,
    "detect_forecast_anomalies": detect_forecast_anomalies,
    "get_sales_history_status": get_sales_history_status,
    "query_planning_data": query_planning_data,
    "send_email": send_email,
    "update_planning_data": update_planning_data,
    "recommend_planning_action": recommend_planning_action,
    "import_master_data": import_master_data,
    }
TOOL_SCHEMAS = [
   {
       "name": "analyze_capacity_bottlenecks",
    "description": "Analyze SAP IBP capacity bottlenecks. Use resource_type='production' for PCAPADEMAND/PCAPAUSAGE, 'storage' or 'handling' for CAPADEMAND/CAPAUSAGE, 'transportation' for TCAPADEMAND/TCAPAUSAGE, or 'all'. Match terms like production capacity, warehouse/storage capacity, goods-receipt/handling capacity, and transport capacity. Returns demand, usage, CAPASUPPLY, consumption rate, shortage, headroom, utilization, planning-level contributors, and affected dimensions. Transportation supply uses the IBP TransResLoc location.",
       "input_schema": {
           "type": "object",
           "properties": {
               "resource_type": {"type": "string", "enum": ["handling", "storage", "production", "transportation", "all"]},
               "resource": {"type": "string", "description": "Optional resource ID returned by IBP"},
               "location": {"type": "string", "description": "Optional location ID"},
               "product": {"type": "string", "description": "Optional product ID"},
               "period_start_rel": {"type": "integer", "description": "Relative period start; 0 is current"},
               "period_end_rel": {"type": "integer", "description": "Relative period end; 3 means the next three periods when start is 1"},
               "utilization_threshold_pct": {"type": "number", "description": "Flag capacity at or above this usage percentage; default 80"},
           },
           "required": [],
       },
   },
   {
       "name": "recommend_capacity_action",
       "description": "Recommend next actions for an IBP handling, storage, production, or transportation capacity issue. This tool is read-only and never changes SAP IBP data. Call it after analyzing a specific bottleneck and pass its resource, location, period, shortage, utilization, and top contributors.",
       "input_schema": {
           "type": "object",
           "properties": {
               "resource_type": {"type": "string", "enum": ["handling", "storage", "production", "transportation"]},
               "resource": {"type": "string"},
               "location": {"type": "string"},
               "period": {"type": "string", "description": "Planning period returned by the analysis"},
               "shortage": {"type": "number"},
               "utilization_pct": {"type": "number"},
               "contributors": {"type": "array", "items": {"type": "object"}},
           },
           "required": ["resource_type", "resource", "location", "period"],
       },
   },
   {
       "name": "update_capacity_supply",
       "description": "Preview or update SAP IBP SUPPLY at resource-location-period level. Always call with confirm=false first and ask for explicit confirmation of the exact resource, location, period, resource type, and supply value. Call with confirm=true only after clear confirmation. Transportation supply must use location TransResLoc.",
       "input_schema": {
           "type": "object",
           "properties": {
               "resource": {"type": "string"},
               "location": {"type": "string"},
               "period": {"type": "string", "description": "Planning period in YYYY-MM format"},
               "supply": {"type": "number", "description": "New available capacity; must be non-negative"},
               "resource_type": {"type": "string", "enum": ["handling", "storage", "production", "transportation"]},
               "version": {"type": "string", "description": "Optional IBP version ID"},
               "confirm": {"type": "boolean", "description": "Must be true only after explicit confirmation"},
           },
           "required": ["resource", "location", "period", "supply", "confirm"],
       },
   },
   {
       "name": "get_forecast_vs_consumption",
    "description": "Compare statistical forecast vs actual consumption. Use alert_direction='over' when the planner says above forecast, 'under' for below forecast, or 'both' for either direction. Use result_scope='product' for product totals or 'combination' for detail.",
       "input_schema": {
           "type": "object",
           "properties": {
               "location": {"type": "string", "description": "Location ID, e.g. '1010'"},
               "product": {"type": "string", "description": "Product ID, e.g. 'Product A'"},
               "customer": {"type": "string", "description": "Customer ID, e.g. 'CUST-100'"},
               "threshold_pct": {"type": "number", "description": "Variance % threshold, default 20"},
               "result_scope": {"type": "string", "enum": ["product", "combination"], "description": "Use product for aggregated product totals, combination for detailed product/location/customer results"},
               "alert_direction": {"type": "string", "enum": ["over", "under", "both"], "description": "Use over for actual above forecast, under for actual below forecast, both for absolute variance"},
               "period_start_rel": {"type": "integer", "description": "Relative period start; 0 is current, 1 is next period"},
               "period_end_rel": {"type": "integer", "description": "Relative period end; use 3 with start 1 for the coming three periods"},
           },
           "required": [],
       },
   },
   {
       "name": "detect_forecast_anomalies",
         "description": "Scan statistical forecast time series for spikes, drops, and flatlines. Include the detected period. Use result_scope='product' to aggregate each product across locations and customers, or 'combination' for separate detail series.",
       "input_schema": {
           "type": "object",
           "properties": {
               "product": {"type": "string", "description": "Optional product ID"},
               "location": {"type": "string", "description": "Optional location ID"},
               "customer": {"type": "string", "description": "Optional customer ID"},
               "sigma_threshold": {"type": "number", "description": "Anomaly threshold in standard deviations, default 3"},
               "flatline_min_periods": {"type": "integer", "description": "Minimum identical periods for a flatline, default 4"},
               "result_scope": {"type": "string", "enum": ["product", "combination"], "description": "Product aggregates across locations and customers; combination keeps separate series"},
           },
       },
   },
   {
       "name": "get_sales_history_status",
    "description": "Check historical sales readiness. Product, location, customer, and target period filters are optional.",
       "input_schema": {
           "type": "object",
           "properties": {
               "target_period": {"type": "string", "description": "YYYY-MM, optional -- defaults to current month"},
               "product": {"type": "string", "description": "Optional product ID"},
               "location": {"type": "string", "description": "Optional location ID"},
               "customer": {"type": "string", "description": "Optional customer ID"},
               "result_scope": {"type": "string", "enum": ["product", "location", "customer", "combination"], "description": "Aggregate ACTUALSQTY by product, location, or customer across the other dimensions; use combination for an exact product/location/customer check"},
           },
       },
   },
   {
       "name": "query_planning_data",
       "description": "Run deterministic analytics over forecast and actual quantities. Use for generic questions such as highest, lowest, total, average, top N, comparisons, and base planning-level results. For base planning level use group_by product, location, customer, and period. Do not pass UOM; the configured data-source UOM is used.",
       "input_schema": {
           "type": "object",
           "properties": {
               "metric": {"type": "string", "enum": ["forecast", "actual", "variance_qty", "variance_pct"], "description": "Quantity or variance to analyze"},
               "aggregation": {"type": "string", "enum": ["sum", "max", "min", "avg"], "description": "Aggregation to calculate"},
               "group_by": {"type": "array", "items": {"type": "string", "enum": ["product", "location", "customer", "period"]}, "description": "Dimensions for grouping; use product for highest product forecast, or product/location/customer/period for base planning level"},
               "location": {"type": "string", "description": "Optional location ID"},
               "product": {"type": "string", "description": "Optional product ID"},
               "customer": {"type": "string", "description": "Optional customer ID"},
               "period_start_rel": {"type": "integer", "description": "Relative period start; 0 is current"},
               "period_end_rel": {"type": "integer", "description": "Relative period end; 0 is current"},
               "threshold_pct": {"type": "number", "description": "Optional absolute variance percentage threshold"},
               "sort": {"type": "string", "enum": ["asc", "desc"]},
               "limit": {"type": "integer", "description": "Number of results, from 1 to 100"},
           },
           "required": [],
       },
   },
   {
       "name": "send_email",
       "description": "Send an email notification to a team about a finding.",
       "input_schema": {
           "type": "object",
           "properties": {
               "recipient": {"type": "string"},
               "subject": {"type": "string"},
               "body": {"type": "string"},
           },
           "required": ["recipient", "subject", "body"],
       },
   },
   {
       "name": "update_planning_data",
    "description": "Import one SAP IBP key-figure row. The user may provide any ordered aggregation_fields and matching aggregation_values, such as CUSTID,PRDID,AOPQTY,PERIODID3_TSTAMP. Always ask for explicit confirmation first.",
       "input_schema": {
           "type": "object",
           "properties": {
               "product": {"type": "string", "description": "Product ID"},
               "location": {"type": "string", "description": "Optional location ID; provide location or customer"},
               "customer": {"type": "string", "description": "Optional customer ID"},
               "period": {"type": "string", "description": "Planning period in YYYY-MM format"},
               "forecast": {"type": "number", "description": "Convenience value for the configured default key figure"},
               "uom": {"type": "string", "description": "Optional unit of measure"},
               "version": {"type": "string", "description": "Optional SAP IBP version ID, for example UPSIDE"},
               "aggregation_fields": {"type": "array", "items": {"type": "string"}, "description": "Ordered fields for AggregationLevelFieldsString"},
               "aggregation_values": {"type": "array", "items": {"type": "object", "properties": {"field": {"type": "string"}, "value": {"type": "string"}}, "required": ["field", "value"]}, "description": "Field/value entries matching aggregation_fields exactly"},
               "confirm": {"type": "boolean", "description": "Must be true only after explicit user confirmation"},
           },
           "required": ["confirm"],
       },
   },
   {
       "name": "recommend_planning_action",
       "description": "Create a business recommendation for forecast over-consumption, under-consumption, spike, drop, or flatline. This tool never changes SAP IBP data.",
       "input_schema": {
           "type": "object",
           "properties": {
               "issue_type": {"type": "string", "enum": ["over_consumption", "under_consumption", "spike", "drop", "flatline"]},
               "product": {"type": "string", "description": "Product ID"},
               "location": {"type": "string", "description": "Optional location ID"},
               "period": {"type": "string", "description": "Optional planning period in YYYY-MM format"},
               "forecast": {"type": "number", "description": "Optional forecast quantity from the finding"},
               "actual": {"type": "number", "description": "Optional actual quantity from the finding"},
               "anomaly_type": {"type": "string", "description": "Optional detected anomaly type"},
               "anomaly_period": {"type": "string", "description": "Optional anomaly period"},
           },
           "required": ["issue_type", "product"],
       },
   },
   {
       "name": "import_master_data",
       "description": "Create, modify, or delete SAP IBP master-data records using the Master Data OData API. Always request explicit confirmation before writing.",
       "input_schema": {
           "type": "object",
           "properties": {
               "master_data_type": {"type": "string", "description": "SAP master data type, for example LOCATION or LOCATIONPRODUCT"},
               "requested_attributes": {"type": "array", "items": {"type": "string"}, "description": "Attributes included in the import"},
               "records": {"type": "array", "items": {"type": "object"}, "description": "One to 5000 master-data records"},
               "planning_area": {"type": "string", "description": "Optional planning area for version-specific master data"},
               "version": {"type": "string", "description": "Optional version ID"},
               "delete_entries": {"type": "boolean", "description": "Delete complete records instead of importing values"},
               "confirm": {"type": "boolean", "description": "Must be true only after explicit user confirmation"},
           },
           "required": ["master_data_type", "requested_attributes", "records", "confirm"],
       },
   },
]

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    insights: list[dict]

def agent_node(state: AgentState) -> dict:
    client = get_llm_client()
    message = client.chat(
        system=SYSTEM_PROMPT,
        messages=state["messages"],
        tools=TOOL_SCHEMAS,
    )
    return {"messages": [message]}

def _message_content(message) -> list:
    return message.content if hasattr(message, "content") else message.get("content", [])

def tool_node(state: AgentState) -> dict:
    last_message = state["messages"][-1]
    tool_results = []
    insights = []
    for block in _message_content(last_message):
        if block.get("type") != "tool_use":
            continue
        fn = TOOL_REGISTRY[block["name"]]
        try:
            result = fn(**block["input"])
        except Exception as exc:  # surface tool errors back to the LLM, don't crash the graph
            result = {"error": str(exc)}
        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": block["id"],
                "name": block["name"],
                "content": json.dumps(result),
            }
        )
        insights.append({"tool": block["name"], "data": result})
    return {
        "messages": [{"role": "user", "content": tool_results}],
        "insights": state.get("insights", []) + insights,
    }

def route_after_agent(state: AgentState) -> str:
    last_message = state["messages"][-1]
    has_tool_call = any(
        block.get("type") == "tool_use" for block in _message_content(last_message)
    )
    return "tool_node" if has_tool_call else END

def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("agent_node", agent_node)
    graph.add_node("tool_node", tool_node)
    graph.set_entry_point("agent_node")
    graph.add_conditional_edges("agent_node", route_after_agent, {"tool_node": "tool_node", END: END})
    graph.add_edge("tool_node", "agent_node")
    return graph.compile()

def run_agent_with_data(user_message: str, history: list[dict] | None = None) -> dict:
    app = build_graph()
    messages = list(history or [])
    messages.append({"role": "user", "content": user_message})
    final_state = app.invoke({"messages": messages, "insights": []})
    last = final_state["messages"][-1]
    text_blocks = [b["text"] for b in _message_content(last) if b.get("type") == "text"]
    return {
        "reply": "\n".join(text_blocks) if text_blocks else str(last),
        "insights": final_state.get("insights", []),
    }


def run_agent(user_message: str) -> str:
    """Keep the original string-returning interface for existing callers."""
    return run_agent_with_data(user_message)["reply"]