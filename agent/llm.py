"""
LLM client wrapper -- provider-agnostic.
Supports Anthropic (Claude), DeepSeek, and Gemini behind one interface. Set
LLM_PROVIDER=anthropic|deepseek|gemini in the environment to choose.
DeepSeek's API is OpenAI-compatible but its tool-calling wire format is
structurally different from Anthropic's (separate `tool_calls` array with
JSON-string arguments, vs. Anthropic's inline `tool_use` content blocks;
tool results go back as standalone `role: "tool"` messages, vs. Anthropic's
`tool_result` content blocks inside a user message).
To keep agent/graph.py unaware of these differences, every client speaks
one COMMON MESSAGE FORMAT internally (this happens to be identical to
Anthropic's native format, since it's already block-based):
   {"role": "assistant", "content": [
       {"type": "text", "text": "..."},
       {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
   ]}
   {"role": "user", "content": [
       {"type": "tool_result", "tool_use_id": "...", "content": "..."},
   ]}
graph.py only ever builds/reads this common format. Each client below is
responsible for translating to/from its provider's actual wire format.
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()

class LLMClient:
   def chat(self, system: str, messages: list, tools: list, max_tokens: int = 1024) -> dict:
       """Returns a single assistant message in the common format above."""
       raise NotImplementedError

# ---------------------------------------------------------------------------
# Anthropic (Claude) -- common format IS Anthropic's native format
# ---------------------------------------------------------------------------
class AnthropicClient(LLMClient):
   def __init__(self):
       import anthropic
       api_key = os.environ.get("ANTHROPIC_API_KEY")
       if not api_key:
           raise RuntimeError("ANTHROPIC_API_KEY not set.")
       self._client = anthropic.Anthropic(api_key=api_key)
       self._model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
   def chat(self, system: str, messages: list, tools: list, max_tokens: int = 1024) -> dict:
       response = self._client.messages.create(
           model=self._model,
           max_tokens=max_tokens,
           system=system,
           messages=messages,
           tools=tools,
       )
       content = []
       for block in response.content:
           if block.type == "text":
               content.append({"type": "text", "text": block.text})
           elif block.type == "tool_use":
               content.append(
                   {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
               )
       return {"role": "assistant", "content": content}

# ---------------------------------------------------------------------------
# DeepSeek -- OpenAI-compatible wire format, translated to/from common format
# ---------------------------------------------------------------------------
class DeepSeekClient(LLMClient):
   def __init__(self):
       from openai import OpenAI
       api_key = os.environ.get("DEEPSEEK_API_KEY")
       if not api_key:
           raise RuntimeError("DEEPSEEK_API_KEY not set.")
       self._client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
       self._model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
   def chat(self, system: str, messages: list, tools: list, max_tokens: int = 1024) -> dict:
       openai_messages = [{"role": "system", "content": system}]
       openai_messages.extend(self._to_openai_messages(messages))
       openai_tools = self._to_openai_tools(tools)
       response = self._client.chat.completions.create(
           model=self._model,
           max_tokens=max_tokens,
           messages=openai_messages,
           tools=openai_tools,
       )
       return self._from_openai_message(response.choices[0].message)
   @staticmethod
   def _to_openai_tools(tools: list) -> list:
       # Anthropic-style {name, description, input_schema} -> OpenAI-style
       # {"type": "function", "function": {name, description, parameters}}
       return [
           {
               "type": "function",
               "function": {
                   "name": t["name"],
                   "description": t["description"],
                   "parameters": t["input_schema"],
               },
           }
           for t in tools
       ]
   @staticmethod
   def _to_openai_messages(messages: list) -> list:
       openai_messages = []
       for msg in messages:
           content = msg.get("content")
           # Plain string content (initial user message) passes through
           if isinstance(content, str):
               openai_messages.append({"role": msg["role"], "content": content})
               continue
           # Assistant message with text and/or tool_use blocks
           if msg["role"] == "assistant":
               text_parts = [b["text"] for b in content if b.get("type") == "text"]
               tool_use_blocks = [b for b in content if b.get("type") == "tool_use"]
               assistant_msg = {"role": "assistant", "content": " ".join(text_parts) or None}
               if tool_use_blocks:
                   assistant_msg["tool_calls"] = [
                       {
                           "id": b["id"],
                           "type": "function",
                           "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                       }
                       for b in tool_use_blocks
                   ]
               openai_messages.append(assistant_msg)
               continue
           # User message carrying tool_result blocks -> becomes separate
           # role:"tool" messages (OpenAI's format), not a single user turn
           tool_result_blocks = [b for b in content if b.get("type") == "tool_result"]
           if tool_result_blocks:
               for b in tool_result_blocks:
                   openai_messages.append(
                       {"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]}
                   )
               continue
           # Fallback: plain text blocks in a user message
           text_parts = [b["text"] for b in content if b.get("type") == "text"]
           openai_messages.append({"role": msg["role"], "content": " ".join(text_parts)})
       return openai_messages
   @staticmethod
   def _from_openai_message(message) -> dict:
       content = []
       if message.content:
           content.append({"type": "text", "text": message.content})
       if message.tool_calls:
           for tc in message.tool_calls:
               content.append(
                   {
                       "type": "tool_use",
                       "id": tc.id,
                       "name": tc.function.name,
                       "input": json.loads(tc.function.arguments),
                   }
               )
       return {"role": "assistant", "content": content}

# ---------------------------------------------------------------------------
# Gemini (Google AI Studio) -- parts-based wire format, translated to/from
# common format. Two structural differences from Anthropic/DeepSeek:
#   - roles are "user" / "model" (not "assistant"), and tool results use
#     role "function"
#   - tool calls/results are matched by function NAME, not a call ID --
#     Gemini's API has no concept of a tool_call_id
# ---------------------------------------------------------------------------
class GeminiClient(LLMClient):
   def __init__(self):
       from google import genai
       api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
       if not api_key:
           raise RuntimeError("GOOGLE_API_KEY (or GEMINI_API_KEY) not set.")
       self._client = genai.Client(api_key=api_key)
       self._model_name = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
   def chat(self, system: str, messages: list, tools: list, max_tokens: int = 1024) -> dict:
       from google.genai import types
       config = types.GenerateContentConfig(
           system_instruction=system,
           tools=[self._to_gemini_tool(tools)],
           max_output_tokens=max_tokens,
       )
       contents = self._to_gemini_contents(messages)
       response = self._client.models.generate_content(
           model=self._model_name, contents=contents, config=config
       )
       return self._from_gemini_response(response)
   @staticmethod
   def _to_gemini_tool(tools: list):
       from google.genai import types
       declarations = [
           types.FunctionDeclaration(
               name=t["name"],
               description=t["description"],
               parameters=GeminiClient._to_gemini_schema(t["input_schema"]),
           )
           for t in tools
       ]
       return types.Tool(function_declarations=declarations)

   @staticmethod
   def _to_gemini_schema(schema: dict) -> dict:
       """Translate JSON Schema type names to Gemini's uppercase enum names."""
       converted = {}
       for key, value in schema.items():
           if key == "type" and isinstance(value, str):
               converted[key] = value.upper()
           elif key == "properties" and isinstance(value, dict):
               converted[key] = {
                   name: GeminiClient._to_gemini_schema(property_schema)
                   for name, property_schema in value.items()
               }
           elif key == "items" and isinstance(value, dict):
               converted[key] = GeminiClient._to_gemini_schema(value)
           else:
               converted[key] = value
       return converted

   @staticmethod
   def _to_gemini_contents(messages: list) -> list:
       from google.genai import types
       contents = []
       for msg in messages:
           if isinstance(msg, dict):
               role = msg.get("role", "user")
               content = msg.get("content")
           else:
               role = "assistant" if getattr(msg, "type", "") == "ai" else "user"
               content = msg.content
           if isinstance(content, str):
               contents.append(types.Content(role="user", parts=[types.Part(text=content)]))
               continue
           if role == "assistant":
               parts = []
               for b in content:
                   if b.get("type") == "text":
                       parts.append(types.Part(text=b["text"]))
                   elif b.get("type") == "tool_use":
                       part_kwargs = {
                           "function_call": types.FunctionCall(
                               name=b["name"], args=b["input"]
                           )
                       }
                       if b.get("thought_signature"):
                           part_kwargs["thought_signature"] = b["thought_signature"]
                       parts.append(types.Part(**part_kwargs))
               contents.append(types.Content(role="model", parts=parts))
               continue
           tool_result_blocks = [b for b in content if b.get("type") == "tool_result"]
           if tool_result_blocks:
               parts = [
                   types.Part.from_function_response(
                       name=b.get("name", "unknown_tool"), response={"result": b["content"]}
                   )
                   for b in tool_result_blocks
               ]
               contents.append(types.Content(role="user", parts=parts))
               continue
           text_parts = [b["text"] for b in content if b.get("type") == "text"]
           contents.append(
               types.Content(role="user", parts=[types.Part(text=" ".join(text_parts))])
           )
       return contents

   @staticmethod
   def _from_gemini_response(response) -> dict:
       content = []
       for part in response.candidates[0].content.parts:
           fc = getattr(part, "function_call", None)
           if fc and getattr(fc, "name", None):
               args = dict(fc.args) if fc.args else {}
               
               ts = getattr(part, "thought_signature", None)
               
               tool_block = {"type": "tool_use", "id": fc.name, "name": fc.name, "input": args}
               if ts:
                   tool_block["thought_signature"] = ts
                   
               content.append(tool_block)
           else:
               text = getattr(part, "text", None)
               if text:
                   content.append({"type": "text", "text": text})
       return {"role": "assistant", "content": content}

# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------
def get_llm_client() -> LLMClient:
   provider = os.environ.get("LLM_PROVIDER", "gemini").lower()
   if provider == "anthropic":
       return AnthropicClient()
   if provider == "deepseek":
       return DeepSeekClient()
   if provider == "gemini":
       return GeminiClient()
   raise ValueError(f"Unknown LLM_PROVIDER: {provider}")