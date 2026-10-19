import unittest
import json
from datetime import date
from unittest.mock import patch

import app as application
from agent import s4_capacity_graph


class S4CapacityRouteTests(unittest.TestCase):
    def setUp(self):
        application.S4_CONVERSATIONS.clear()
        application.CONVERSATIONS.clear()

    def test_s4_route_uses_separate_conversation_store(self):
        with patch.object(
            application,
            "run_s4_agent_with_data",
            side_effect=[
                {"reply": "Capacity read", "insights": []},
                {"reply": "Follow-up", "insights": []},
            ],
        ) as run_s4:
            first = application.ask_s4(
                application.AskRequest(message="Read RESOR_01 capacity", conversation_id="s4-1")
            )
            second = application.ask_s4(
                application.AskRequest(message="Show its shifts", conversation_id="s4-1")
            )

        self.assertEqual(first.conversation_id, "s4-1")
        self.assertEqual(second.reply, "Follow-up")
        self.assertEqual(run_s4.call_args_list[0].kwargs["history"], [])
        self.assertEqual(len(run_s4.call_args_list[1].kwargs["history"]), 2)
        self.assertNotIn("s4-1", application.CONVERSATIONS)

    def test_cnc04_north_sea_bottleneck_returns_temporary_fixed_reply(self):
        user_message = (
            "We have a bottleneck on Work Center CNC-04 for the upcoming "
            "North Sea rig shipment. What is the impact, and how do we fix it "
            "without missing the vessel cut-off?"
        )
        with patch.object(s4_capacity_graph, "get_llm_client") as get_llm_client:
            result = s4_capacity_graph.run_s4_agent_with_data(user_message)

        self.assertEqual(
            result["reply"], s4_capacity_graph.CAPACITY_BOTTLENECK_DEMO_REPLY
        )
        self.assertEqual(
            result["insights"][0]["data"],
            s4_capacity_graph.CAPACITY_BOTTLENECK_DEMO_EVIDENCE,
        )
        get_llm_client.assert_not_called()

    def test_cnc04_bottleneck_followup_returns_solution_plan(self):
        history = [
            {
                "role": "user",
                "content": (
                    "We have a bottleneck on Work Center CNC-04 for the upcoming "
                    "North Sea rig shipment."
                ),
            }
        ]
        with patch.object(s4_capacity_graph, "get_llm_client") as get_llm_client:
            result = s4_capacity_graph.run_s4_agent_with_data(
                "What solution can be added for the above problem?", history=history
            )

        self.assertEqual(
            result["reply"], s4_capacity_graph.CAPACITY_BOTTLENECK_SOLUTION_REPLY
        )
        self.assertEqual(
            result["insights"][0]["data"],
            s4_capacity_graph.CAPACITY_BOTTLENECK_SOLUTION_EVIDENCE,
        )
        get_llm_client.assert_not_called()

    def test_approval_returns_mock_sap_pp_resolution(self):
        history = [
            {
                "role": "user",
                "content": (
                    "We have a bottleneck on Work Center CNC-04 for the upcoming "
                    "North Sea rig shipment."
                ),
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": s4_capacity_graph.CAPACITY_BOTTLENECK_SOLUTION_REPLY,
                    }
                ],
            },
        ]
        with patch.object(s4_capacity_graph, "get_llm_client") as get_llm_client:
            result = s4_capacity_graph.run_s4_agent_with_data("Yes", history=history)

        self.assertEqual(
            result["reply"], s4_capacity_graph.CAPACITY_BOTTLENECK_RESOLUTION_REPLY
        )
        evidence = result["insights"][0]["data"]
        self.assertEqual(evidence["alternate_work_center"], "CNC-05")
        self.assertEqual(evidence["resolution_status"], "Completed")
        get_llm_client.assert_not_called()

    def test_relative_next_days_use_server_today_and_inclusive_day_count(self):
        with patch.object(s4_capacity_graph, "date") as mocked_date:
            mocked_date.today.return_value = date(2026, 10, 19)
            result = s4_capacity_graph._relative_capacity_date_range(
                "Is there a bottleneck in coming next 3 days?"
            )

        self.assertEqual(result, ("2026-10-19", "2026-10-21"))

    def test_explicit_date_range_is_not_replaced_by_relative_range(self):
        self.assertIsNone(
            s4_capacity_graph._relative_capacity_date_range(
                "Check capacity from 19/10/2026 to 23/10/2026 over next 3 days"
            )
        )

    def test_capacity_model_summary_is_compact_but_insight_keeps_full_result(self):
        full_result = {
            "status": "completed",
            "read_only": True,
            "source": "SAP evaluated capacity API",
            "evaluation": {"start_date": "2026-10-19", "end_date": "2026-10-21"},
            "capacity_evaluations": [
                {
                    "date": f"202610{day}",
                    "plant": "PL01",
                    "work_center": f"WC{day}",
                    "status": "overloaded" if day == "19" else "within_capacity",
                    "recommended_actions": ["Verify with planner"],
                }
                for day in ("19", "20", "21")
            ],
            "evaluated_bucket_dates": ["20261019", "20261020", "20261021"],
            "contributors": [
                {
                    "date": "20261019",
                    "plant": "PL01",
                    "work_center": "WC19",
                    "order_id": f"{order_id:06d}",
                }
                for order_id in range(250)
            ],
            "evaluation_count": 3,
            "contributor_count": 250,
            "recommendations": [],
        }
        state = {
            "messages": [
                {"role": "user", "content": "Check capacity in coming next 3 days"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "analyze_s4_capacity_load",
                            "input": {},
                        }
                    ],
                },
            ],
            "insights": [],
        }
        with patch.dict(
            s4_capacity_graph.TOOL_REGISTRY,
            {"analyze_s4_capacity_load": lambda **kwargs: full_result},
        ):
            result = s4_capacity_graph._tool_node(state)

        model_payload = json.loads(result["messages"][0]["content"][0]["content"])
        self.assertEqual(model_payload["evaluation_count"], 3)
        self.assertEqual(len(model_payload["overloaded_contributors"]), 40)
        self.assertEqual(model_payload["overloaded_contributors_omitted"], 210)
        self.assertNotIn("capacity_evaluations", model_payload)
        self.assertEqual(result["insights"][0]["data"], full_result)

    def test_tool_call_uses_today_based_dates_for_relative_request(self):
        captured_inputs = []
        state = {
            "messages": [
                {"role": "user", "content": "Any bottleneck in coming next 3 days?"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "analyze_s4_capacity_load",
                            "input": {"bucket_type": "D"},
                        }
                    ],
                },
            ],
            "insights": [],
        }
        with patch.object(s4_capacity_graph, "date") as mocked_date:
            mocked_date.today.return_value = date(2026, 10, 19)
            with patch.dict(
                s4_capacity_graph.TOOL_REGISTRY,
                {
                    "analyze_s4_capacity_load": lambda **kwargs: (
                        captured_inputs.append(kwargs) or {"capacity_evaluations": []}
                    )
                },
            ):
                s4_capacity_graph._tool_node(state)

        self.assertEqual(captured_inputs[0]["start_date"], "2026-10-19")
        self.assertEqual(captured_inputs[0]["end_date"], "2026-10-21")

    def test_s4_model_history_is_limited_to_recent_messages(self):
        captured = {}

        class Client:
            def chat(self, **kwargs):
                captured.update(kwargs)
                return {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}

        history = [
            {"role": "user", "content": f"question {index}"}
            for index in range(s4_capacity_graph.MAX_S4_HISTORY_MESSAGES + 5)
        ]
        with patch.object(s4_capacity_graph, "get_llm_client", return_value=Client()):
            s4_capacity_graph.run_s4_agent_with_data("current question", history=history)
        self.assertEqual(
            len(captured["messages"]),
            s4_capacity_graph.MAX_S4_HISTORY_MESSAGES + 1,
        )
        self.assertEqual(captured["messages"][0].content, "question 5")
        self.assertEqual(captured["messages"][-1].content, "current question")


if __name__ == "__main__":
    unittest.main()