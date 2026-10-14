import unittest
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


if __name__ == "__main__":
    unittest.main()