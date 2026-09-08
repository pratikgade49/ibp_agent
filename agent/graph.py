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
from typing import Annotated, TypedDict
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from agent.llm import get_llm_client
from agent.tools import (
   detect_forecast_anomalies,
   get_forecast_vs_consumption,
   get_sales_history_status,
    query_planning_data,
   send_email,
)
SYSTEM_PROMPT = """You are the IBP Demand Planning Agent. You help demand \
planners validate data, monitor consumption variance, and detect forecast \
anomalies in SAP Integrated Business Planning.
You have five tools available. Decide which tool(s) to call and in what \
order based on the planner's request -- do not guess numbers yourself, \
always call the relevant tool to get real data first.
Use query_planning_data for generic questions involving totals, rankings, \
maximums, minimums, averages, comparisons, or base planning-level results. \
For base planning level, group by product, location, customer, and period \
unless the planner specifies a different grouping. Do not include UOM as a \
query argument; the configured data-source UOM is used.
When a check reveals a problem (variance over threshold, anomalies found, \
or missing sales data), offer to send an email notification via the \
send_email tool, but only send it if the user confirms.
When a tool returns multiple results, report the summary counts and list every
item in alert_results in a compact markdown table with period, product, location,
customer, forecast, actual, variance, and direction. Do not report only the
total count. The tool payload contains alert_results only, not every evaluated
row."""
TOOL_REGISTRY = {
   "get_forecast_vs_consumption": get_forecast_vs_consumption,
   "detect_forecast_anomalies": detect_forecast_anomalies,
   "get_sales_history_status": get_sales_history_status,
    "query_planning_data": query_planning_data,
   "send_email": send_email,
}
TOOL_SCHEMAS = [
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
]

class AgentState(TypedDict):
   messages: Annotated[list, add_messages]

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
               "content": str(result),
           }
       )
   return {"messages": [{"role": "user", "content": tool_results}]}

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

def run_agent(user_message: str) -> str:
   app = build_graph()
   final_state = app.invoke({"messages": [{"role": "user", "content": user_message}]})
   last = final_state["messages"][-1]
   text_blocks = [b["text"] for b in _message_content(last) if b.get("type") == "text"]
   return "\n".join(text_blocks) if text_blocks else str(last)