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
   send_email,
)
SYSTEM_PROMPT = """You are the IBP Demand Planning Agent. You help demand \
planners validate data, monitor consumption variance, and detect forecast \
anomalies in SAP Integrated Business Planning.
You have four tools available. Decide which tool(s) to call and in what \
order based on the planner's request -- do not guess numbers yourself, \
always call the relevant tool to get real data first.
When a check reveals a problem (variance over threshold, anomalies found, \
or missing sales data), offer to send an email notification via the \
send_email tool, but only send it if the user confirms."""
TOOL_REGISTRY = {
   "get_forecast_vs_consumption": get_forecast_vs_consumption,
   "detect_forecast_anomalies": detect_forecast_anomalies,
   "get_sales_history_status": get_sales_history_status,
   "send_email": send_email,
}
TOOL_SCHEMAS = [
   {
       "name": "get_forecast_vs_consumption",
    "description": "Compare statistical forecast vs actual consumption for optional product and location filters. Omit either filter to include all matching products or locations.",
       "input_schema": {
           "type": "object",
           "properties": {
               "location": {"type": "string", "description": "Location ID, e.g. '1010'"},
               "product": {"type": "string", "description": "Product ID, e.g. 'Product A'"},
               "threshold_pct": {"type": "number", "description": "Variance % threshold, default 20"},
           },
           "required": [],
       },
   },
   {
       "name": "detect_forecast_anomalies",
       "description": "Scan statistical forecast time series for spikes, drops, and flatlines across products.",
       "input_schema": {
           "type": "object",
           "properties": {
               "sigma_threshold": {"type": "number", "description": "Anomaly threshold in standard deviations, default 3"},
               "flatline_min_periods": {"type": "integer", "description": "Minimum identical periods for a flatline, default 4"},
           },
       },
   },
   {
       "name": "get_sales_history_status",
       "description": "Check whether historical sales data is fully loaded through the target period.",
       "input_schema": {
           "type": "object",
           "properties": {
               "target_period": {"type": "string", "description": "YYYY-MM, optional -- defaults to current month"},
           },
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