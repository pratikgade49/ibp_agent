import unittest
from unittest.mock import patch

import app as application


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


if __name__ == "__main__":
    unittest.main()