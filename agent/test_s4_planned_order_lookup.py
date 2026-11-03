import unittest
from unittest.mock import patch

from agent.s4_capacity_tools import (
    S4_PLANNED_ORDERS_ROOT,
    _schedule_date_value,
    delete_s4_planned_orders,
    read_s4_planned_orders_for_material,
)
from agent import s4_capacity_graph


class PlannedOrderMaterialLookupTests(unittest.TestCase):
    def test_schedule_date_parser_handles_sap_odata_epoch_date(self):
        self.assertEqual(
            _schedule_date_value("/Date(1793664000000)/"),
            "2026-11-03",
        )

    def test_filters_by_material_and_mrp_plant_not_work_center_or_date(self):
        order_payload = {"d": {"results": [{
            "PlannedOrder": "4000004820",
            "PlannedOrderType": "LA",
            "Material": "F-10A",
            "ProductionPlant": "1710",
            "MRPPlant": "1710",
            "PlndOrderPlannedStartDate": "2026-11-02T00:00:00",
            "PlndOrderPlannedEndDate": "2026-11-05T00:00:00",
        }]}}
        capacity_payload = {"d": {"results": [{
            "PlannedOrder": "4000004820",
            "Operation": "0010",
            "MRPPlant": "1710",
            "WorkCenter": "WCEN003",
            "OperationEarliestStartDate": "2026-11-02T00:00:00",
            "OperationEarliestEndDate": "2026-11-03T00:00:00",
            "CapacityRequirement": "10000002810",
            "CapacityRequirementUnit": "HR",
        }]}}
        with patch(
            "agent.s4_capacity_tools._s4_get",
            side_effect=[order_payload, capacity_payload],
        ) as s4_get:
            result = read_s4_planned_orders_for_material("F-10A", "1710")

        query = s4_get.call_args_list[0].args[1]
        self.assertIn("Material eq 'F-10A'", query["$filter"])
        self.assertIn("MRPPlant eq '1710'", query["$filter"])
        self.assertNotIn("WorkCenter", query["$filter"])
        self.assertNotIn("Date", query["$filter"])
        self.assertEqual(
            s4_get.call_args_list[0].kwargs["service_root"],
            S4_PLANNED_ORDERS_ROOT,
        )
        self.assertEqual(result["planned_order_count"], 1)
        planned_order = result["planned_orders"][0]
        self.assertEqual(planned_order["planned_order"], "4000004820")
        self.assertEqual(planned_order["capacity_assignments"][0]["work_center"], "WCEN003")
        self.assertEqual(planned_order["capacity_assignments"][0]["operation_start_date"], "2026-11-02")

    def test_successful_reassignment_triggers_same_material_mrp_plant_lookup(self):
        update_result = {
            "status": "updated",
            "material": "F-10A",
            "plant": "1710",
        }
        planned_order_result = {
            "status": "completed",
            "planned_order_count": 1,
            "planned_orders": [{"planned_order": "4000004820"}],
        }
        messages = [
            {"role": "user", "content": "Move the production order"},
            {"role": "assistant", "content": [{
                "type": "tool_use",
                "id": "call-1",
                "name": "update_s4_production_order_operation_work_center",
                "input": {},
            }]},
        ]
        with (
            patch.dict(
                s4_capacity_graph.TOOL_REGISTRY,
                {"update_s4_production_order_operation_work_center": lambda **_: update_result},
            ),
            patch.object(
                s4_capacity_graph,
                "read_s4_planned_orders_for_material",
                return_value=planned_order_result,
            ) as planned_order_lookup,
        ):
            result = s4_capacity_graph._tool_node({"messages": messages, "insights": []})

        planned_order_lookup.assert_called_once_with(material="F-10A", mrp_plant="1710")
        self.assertEqual(
            result["insights"][0]["data"]["planned_order_follow_up"],
            planned_order_result,
        )

    def test_automatic_alternate_check_normalizes_sap_epoch_date(self):
        capacity_result = {
            "capacity_evaluations": [{
                "date": "/Date(1793664000000)/",
                "plant": "PL01",
                "work_center": "PPDS_WC3",
                "status": "overloaded",
            }],
            "contributors": [{
                "date": "/Date(1793664000000)/",
                "plant": "PL01",
                "work_center": "PPDS_WC3",
                "order_id": "000001001893",
                "operation": "0010",
                "material": "FG_PPDS",
            }],
        }
        with patch.object(
            s4_capacity_graph,
            "recommend_s4_routing_alternates",
            return_value={"status": "completed", "alternates": []},
        ) as recommend:
            result = s4_capacity_graph._assess_overloaded_alternates(capacity_result)

        recommend.assert_called_once_with(
            plant="PL01",
            work_center="PPDS_WC3",
            order_id="000001001893",
            operation="0010",
            material="FG_PPDS",
            evaluation_date="2026-11-03",
        )
        self.assertEqual(result[0]["date"], "2026-11-03")

    def test_batch_delete_preview_is_read_only_and_returns_exact_phrase(self):
        scoped_orders = {"d": {"results": [
            {
                "PlannedOrder": "4000009336",
                "Material": "FG_PPDS",
                "MRPPlant": "PL01",
                "ProductionPlant": "PL01",
            },
            {
                "PlannedOrder": "4000009338",
                "Material": "FG_PPDS",
                "MRPPlant": "PL01",
                "ProductionPlant": "PL01",
            },
        ]}}
        with (
            patch("agent.s4_capacity_tools._s4_get", return_value=scoped_orders),
            patch("agent.s4_capacity_tools.requests.delete") as sap_delete,
        ):
            result = delete_s4_planned_orders(
                "FG_PPDS", "PL01", ["4000009336", "4000009338"]
            )

        self.assertEqual(result["status"], "approval_required")
        self.assertTrue(result["read_only"])
        self.assertEqual(
            result["approval_phrase"],
            "CONFIRM DELETE PLANNED ORDERS 4000009336, 4000009338 FOR FG_PPDS AT PL01",
        )
        sap_delete.assert_not_called()

    def test_confirmed_batch_delete_revalidates_scope_and_verifies_each_order(self):
        scoped_orders = {"d": {"results": [
            {"PlannedOrder": "4000009336", "Material": "FG_PPDS", "MRPPlant": "PL01"},
            {"PlannedOrder": "4000009338", "Material": "FG_PPDS", "MRPPlant": "PL01"},
        ]}}
        csrf_responses = []
        for order_id in ("4000009336", "4000009338"):
            response = unittest.mock.Mock(
                ok=True,
                headers={"x-csrf-token": f"token-{order_id}", "ETag": f'W/"{order_id}"'},
                cookies={"session": order_id},
            )
            response.json.return_value = {
                "d": {"Material": "FG_PPDS", "MRPPlant": "PL01"},
            }
            csrf_responses.extend((response, unittest.mock.Mock(ok=False, status_code=404)))

        with (
            patch("agent.s4_capacity_tools._s4_get", return_value=scoped_orders),
            patch(
                "agent.s4_capacity_tools._s4_request_config",
                return_value=("https://s4.example", {"timeout": 1}),
            ),
            patch("agent.s4_capacity_tools.requests.get", side_effect=csrf_responses),
            patch("agent.s4_capacity_tools.requests.delete", return_value=unittest.mock.Mock(ok=True)) as sap_delete,
        ):
            result = delete_s4_planned_orders(
                "FG_PPDS",
                "PL01",
                ["4000009336", "4000009338"],
                "CONFIRM DELETE PLANNED ORDERS 4000009336, 4000009338 FOR FG_PPDS AT PL01",
            )

        self.assertEqual(result["status"], "deleted")
        self.assertEqual(
            [item["status"] for item in result["deletion_results"]],
            ["deleted", "deleted"],
        )
        self.assertEqual(sap_delete.call_count, 2)

    def test_graph_discards_unmatched_planned_order_delete_approval(self):
        seen_inputs = []

        def capture_tool_input(**tool_input):
            seen_inputs.append(tool_input)
            return {"status": "approval_required", "read_only": True}

        messages = [
            {"role": "user", "content": "Please delete these planned orders"},
            {"role": "assistant", "content": [{
                "type": "tool_use",
                "id": "delete-call",
                "name": "delete_s4_planned_orders",
                "input": {
                    "material": "FG_PPDS",
                    "mrp_plant": "PL01",
                    "planned_order_ids": ["4000009336", "4000009338"],
                    "approval_phrase": "CONFIRM DELETE PLANNED ORDERS 4000009336, 4000009338 FOR FG_PPDS AT PL01",
                },
            }]},
        ]
        with patch.dict(
            s4_capacity_graph.TOOL_REGISTRY,
            {"delete_s4_planned_orders": capture_tool_input},
        ):
            s4_capacity_graph._tool_node({"messages": messages, "insights": []})

        self.assertEqual(seen_inputs[0]["approval_phrase"], "")


if __name__ == "__main__":
    unittest.main()