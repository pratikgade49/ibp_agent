import unittest
from unittest.mock import patch

from customer_base_agent.graph import _tool_result_for_model, run_customer_base_agent


class ToolResultCompactionTests(unittest.TestCase):
    def test_prepared_forecast_rows_and_excess_warnings_are_omitted(self):
        result = {
            "status": "prepared",
            "planning_version_id": "",
            "scenario_id": "SCENARIO",
            "row_count": 4480,
            "forecast_rows": [{"period": "2026-10"}] * 4480,
            "warnings": [f"warning {index}" for index in range(50)],
            "approval_phrase": "APPROVE CUSTOMER BASE RUN ABC123",
        }

        summary = _tool_result_for_model("prepare_customer_base_forecast", result)

        self.assertNotIn("forecast_rows", summary)
        self.assertEqual(summary["row_count"], 4480)
        self.assertEqual(summary["warning_count"], 50)
        self.assertEqual(len(summary["warnings"]), 10)

    def test_non_prepared_results_are_unchanged(self):
        result = {"status": "error", "error": "IBP unavailable"}
        self.assertEqual(_tool_result_for_model("prepare_customer_base_forecast", result), result)

    def test_prepare_finishes_without_second_model_call_and_keeps_ui_rows(self):
        forecast_rows = [{"period": f"2026-{month:02d}", "customer_base_forecast": month} for month in range(1, 101)]
        prepared = {
            "status": "prepared",
            "planning_version_id": "",
            "scenario_id": "SCENARIO",
            "row_count": len(forecast_rows),
            "forecast_rows": forecast_rows,
            "warnings": [],
            "approval_phrase": "APPROVE CUSTOMER BASE RUN ABC123",
            "note": "No write submitted",
        }

        class FakeClient:
            def __init__(self):
                self.calls = []

            def chat(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "role": "assistant",
                    "content": [{
                        "type": "tool_use",
                        "id": "prepare-call",
                        "name": "prepare_customer_base_forecast",
                        "input": {},
                    }],
                }

        client = FakeClient()
        history = [{"role": "user", "content": f"old message {index} " + ("x" * 5000)} for index in range(7)]
        with patch("customer_base_agent.graph.get_llm_client", return_value=client), patch.dict(
            "customer_base_agent.graph.TOOL_REGISTRY",
            {"prepare_customer_base_forecast": lambda **kwargs: prepared},
        ):
            result = run_customer_base_agent(
                "prepare forecast",
                requester="planner",
                history=history,
            )

        self.assertEqual(len(client.calls), 1)
        self.assertLessEqual(len(client.calls[0]["messages"]), 7)
        self.assertLessEqual(len(client.calls[0]["messages"][0].content), 4000)
        self.assertEqual(result["insights"][0]["data"]["forecast_rows"], forecast_rows)
        self.assertIn(prepared["approval_phrase"], result["reply"])


if __name__ == "__main__":
    unittest.main()