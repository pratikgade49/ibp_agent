import unittest
from unittest.mock import patch

import app as application
from agent.sales_assurance import run_sales_assurance_with_data


class SalesAssuranceTests(unittest.TestCase):
    def setUp(self):
        application.SALES_CONVERSATIONS.clear()

    def test_analysis_distinguishes_exposed_order_value_from_lost_revenue(self):
        result = run_sales_assurance_with_data(
            "Which orders are impacted by the production interruption?"
        )

        self.assertIn("not lost revenue", result["reply"])
        evidence = result["insights"][0]["data"]
        self.assertEqual(evidence["data_status"], "Illustrative scenario; not live SAP data")
        self.assertEqual(evidence["order_value_exposed"], "$2.4M")
        self.assertEqual(len(evidence["orders"]), 3)
        self.assertEqual(evidence["disruption"]["expected_recovery"], "Not available")
        self.assertIn("checks required", evidence["alternatives"][1]["constraint"])

    def test_approval_is_recorded_without_submitting_an_sap_update(self):
        history = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Rotterdam plant is a candidate for review."}],
            }
        ]
        result = run_sales_assurance_with_data("Approve recommendation", history)

        self.assertIn("No SAP sales document", result["reply"])
        evidence = result["insights"][0]["data"]["recommendation"]
        self.assertEqual(evidence["approval_status"], "Approval recorded for review")
        self.assertEqual(evidence["sap_update_status"], "No SAP sales document update submitted")

    def test_approval_from_scenario_board_records_review_not_execution(self):
        result = run_sales_assurance_with_data("Approve recommendation", history=[])

        self.assertIn("approval has been recorded", result["reply"].casefold())
        self.assertIn("No SAP sales document", result["reply"])
        self.assertEqual(
            result["insights"][0]["data"]["recommendation"]["approval_status"],
            "Approval recorded for review",
        )

    def test_sales_route_uses_separate_conversation_store(self):
        with patch.object(
            application,
            "run_sales_assurance_with_data",
            side_effect=[
                {"reply": "Initial analysis", "insights": []},
                {"reply": "Follow-up", "insights": []},
            ],
        ) as run_sales:
            application.ask_sales(
                application.AskRequest(message="Analyze the interruption", conversation_id="sales-1")
            )
            response = application.ask_sales(
                application.AskRequest(message="Compare the options", conversation_id="sales-1")
            )

        self.assertEqual(response.reply, "Follow-up")
        self.assertEqual(run_sales.call_args_list[0].kwargs["history"], [])
        self.assertEqual(len(run_sales.call_args_list[1].kwargs["history"]), 2)
        self.assertNotIn("sales-1", application.CONVERSATIONS)


if __name__ == "__main__":
    unittest.main()