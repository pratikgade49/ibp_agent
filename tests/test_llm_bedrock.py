import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import llm
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


class BedrockProviderTests(unittest.TestCase):
    def test_translates_common_messages_and_tools_to_converse(self):
        tool_schema = {
            "name": "read_capacity",
            "description": "Read capacity",
            "input_schema": {
                "type": "object",
                "properties": {"plant": {"type": "string"}},
            },
        }
        messages = [
            {"role": "user", "content": "Read plant PL01"},
            {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read_capacity",
                    "input": {"plant": "PL01"},
                }],
            },
            {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": "{\"status\":\"completed\"}",
                }],
            },
        ]
        client = Mock()
        client.converse.return_value = {
            "output": {"message": {"role": "assistant", "content": [{"text": "Done"}]}}
        }
        bedrock = llm.BedrockClient.__new__(llm.BedrockClient)
        bedrock._client = client
        bedrock._model = "test-model"

        result = bedrock.chat("System prompt", messages, [tool_schema], max_tokens=512)

        request = client.converse.call_args.kwargs
        self.assertEqual(request["modelId"], "test-model")
        self.assertEqual(request["system"], [{"text": "System prompt"}])
        self.assertEqual(request["inferenceConfig"], {"maxTokens": 512})
        self.assertEqual(request["messages"][0]["content"], [{"text": "Read plant PL01"}])
        self.assertEqual(request["messages"][1]["content"][0]["toolUse"]["toolUseId"], "call-1")
        self.assertEqual(request["messages"][2]["content"][0]["toolResult"]["toolUseId"], "call-1")
        self.assertEqual(request["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"], tool_schema["input_schema"])
        self.assertEqual(result, {"role": "assistant", "content": [{"type": "text", "text": "Done"}]})

    def test_normalizes_bedrock_tool_use_response(self):
        result = llm.BedrockClient._from_bedrock_response({
            "output": {"message": {"content": [
                {"text": "Checking"},
                {"toolUse": {"toolUseId": "id-1", "name": "read_capacity", "input": {"plant": "PL01"}}},
            ]}}
        })
        self.assertEqual(result["content"][1], {
            "type": "tool_use",
            "id": "id-1",
            "name": "read_capacity",
            "input": {"plant": "PL01"},
        })

    def test_translates_langchain_messages_received_from_langgraph(self):
        messages = [
            HumanMessage(content="Read capacity for PL01"),
            AIMessage(content=[{
                "type": "tool_use",
                "id": "call-1",
                "name": "read_capacity",
                "input": {"plant": "PL01"},
            }]),
            ToolMessage(content='{"status":"completed"}', tool_call_id="call-1"),
        ]

        result = llm.BedrockClient._to_bedrock_messages(messages)

        self.assertEqual(result[0], {
            "role": "user",
            "content": [{"text": "Read capacity for PL01"}],
        })
        self.assertEqual(result[1]["role"], "assistant")
        self.assertEqual(result[1]["content"][0]["toolUse"]["toolUseId"], "call-1")
        self.assertEqual(result[2], {
            "role": "user",
            "content": [{
                "toolResult": {
                    "toolUseId": "call-1",
                    "content": [{"text": '{"status":"completed"}'}],
                }
            }],
        })

    @patch.dict(os.environ, {"LLM_PROVIDER": "bedrock"})
    @patch.object(llm, "BedrockClient")
    def test_provider_selector_returns_bedrock_for_both_graphs(self, bedrock_client):
        self.assertIs(llm.get_llm_client(), bedrock_client.return_value)


if __name__ == "__main__":
    unittest.main()