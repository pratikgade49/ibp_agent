import unittest
from unittest.mock import patch

from agent.s4_capacity_tools import (
    S4_PLANNED_ORDERS_ROOT,
    _capacity_fit,
    _schedule_date_value,
    analyze_s4_capacity_load,
    delete_s4_planned_orders,
    recommend_s4_routing_alternates,
    read_s4_planned_orders_for_material,
)
from agent import s4_capacity_graph


class PlannedOrderMaterialLookupTests(unittest.TestCase):
    def test_capacity_fit_accepts_equivalent_hour_units(self):
        result = _capacity_fit(
            [{"remaining_capacity": 8.0, "capacity_unit": "H"}],
            8.0,
            "HR",
        )

        self.assertTrue(result["sufficient_for_operation"])
        self.assertEqual(result["remaining_after_move"], 0.0)

    def test_capacity_fit_does_not_compare_incompatible_units(self):
        result = _capacity_fit(
            [{"remaining_capacity": 480.0, "capacity_unit": "MIN"}],
            8.0,
            "HR",
        )

        self.assertIsNone(result["sufficient_for_operation"])

    def test_routing_alternate_uses_known_operation_capacity(self):
        finder_payload = {"d": {"results": [{
            "Aufnr": "000001001821",
            "Vornr": "0020",
            "Matnr": "3000250",
            "MatchStatus": "MATCH",
            "AltWerks": "PL01",
            "AltArbpl": "WCEN005",
        }]}}
        with (
            patch("agent.s4_capacity_tools._s4_get", return_value=finder_payload),
            patch(
                "agent.s4_capacity_tools.analyze_s4_capacity_load",
                return_value={"capacity_evaluations": [{
                    "remaining_capacity": 8.0,
                    "capacity_unit": "H",
                    "status": "within_capacity",
                }]},
            ),
            patch("agent.s4_capacity_tools._operation_required_capacity") as read_source_capacity,
        ):
            result = recommend_s4_routing_alternates(
                plant="PL01",
                work_center="WCEN009",
                order_id="000001001821",
                operation="0020",
                material="3000250",
                evaluation_date="2026-11-11",
                required_capacity=8.0,
                required_unit="HR",
            )

        read_source_capacity.assert_not_called()
        alternate = result["alternates"][0]
        self.assertEqual(alternate["work_center"], "WCEN005")
        self.assertTrue(alternate["sufficient_for_operation"])
        self.assertEqual(alternate["remaining_after_move"], 0.0)

    def test_schedule_date_parser_handles_sap_odata_epoch_date(self):
        self.assertEqual(
            _schedule_date_value("/Date(1793664000000)/"),
            "2026-11-03",
        )
        self.assertEqual(
            _schedule_date_value("/Date(1793664000000+0000)/"),
            "2026-11-03",
        )
        self.assertEqual(
            _schedule_date_value("20261117"),
            "2026-11-17",
        )

    def test_capacity_load_normalizes_compact_dates_for_buckets_and_contributors(self):
        aggregate_payload = {"d": {"results": [{
            "CapacityEvaluationTimePeriod": "20261117",
            "Plant": "PL01",
            "WorkCenter": "WCEN009",
            "WorkCenterCapRqmtInCapUnit": "16.0",
            "WorkCenterAvailableCapacity": "8.0",
            "WrkCtrRmngCapInCapUnit": "-8.0",
            "WorkCenterCapacityUnit": "H",
        }]}}
        contributor_payload = {"d": {"results": [{
            "CapacityEvaluationTimePeriod": "20261117",
            "Plant": "PL01",
            "WorkCenter": "WCEN009",
            "OrderID": "000001001821",
            "Operation": "0020",
            "Material": "3000250",
            "WorkCenterCapRqmtInCapUnit": "8.0",
            "WorkCenterCapacityUnit": "H",
        }]}}
        with patch(
            "agent.s4_capacity_tools._s4_get",
            side_effect=[aggregate_payload, contributor_payload],
        ):
            result = analyze_s4_capacity_load(
                plant="PL01",
                work_center="WCEN009",
                start_date="2026-11-17",
                end_date="2026-11-17",
            )

        self.assertEqual(result["capacity_evaluations"][0]["date"], "2026-11-17")
        self.assertEqual(result["contributors"][0]["date"], "2026-11-17")
        self.assertEqual(result["evaluated_bucket_dates"], ["2026-11-17"])

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

    def test_automatic_alternate_check_normalizes_sap_compact_date(self):
        capacity_result = {
            "capacity_evaluations": [{
                "date": "20261117",
                "plant": "PL01",
                "work_center": "PPDS_WC3",
                "status": "overloaded",
            }],
            "contributors": [{
                "date": "20261117",
                "plant": "PL01",
                "work_center": "PPDS_WC3",
                "order_id": "000001001893",
                "operation": "0010",
                "material": "FG_PPDS",
                "required_capacity": 8.0,
                "capacity_unit": "H",
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
            evaluation_date="2026-11-17",
            required_capacity=8.0,
            required_unit="H",
        )
        self.assertEqual(result[0]["date"], "2026-11-17")

    def test_single_capacity_tool_call_includes_automatic_alternate_assessment(self):
        capacity_result = {
            "status": "completed",
            "capacity_evaluations": [{
                "date": "2026-11-17",
                "plant": "PL01",
                "work_center": "WCEN009",
                "required_capacity": 16.0,
                "available_capacity": 8.0,
                "remaining_capacity": -8.0,
                "capacity_unit": "H",
                "status": "overloaded",
            }],
            "contributors": [{
                "date": "2026-11-17",
                "plant": "PL01",
                "work_center": "WCEN009",
                "order_id": "000001001821",
                "operation": "0020",
                "material": "3000250",
                "required_capacity": 8.0,
                "capacity_unit": "H",
            }],
        }
        alternate_result = {
            "status": "completed",
            "alternate_count": 1,
            "alternates": [{
                "work_center": "WCEN005",
                "plant": "PL01",
                "capacity_status": "within_capacity",
                "sufficient_for_operation": True,
                "remaining_after_move": 0.0,
            }],
        }
        messages = [
            {"role": "user", "content": (
                "Is there a capacity bottleneck, and what alternate work center "
                "is available for affected orders?"
            )},
            {"role": "assistant", "content": [{
                "type": "tool_use",
                "id": "capacity-call",
                "name": "analyze_s4_capacity_load",
                "input": {"start_date": "2026-11-17", "end_date": "2026-11-17"},
            }]},
        ]
        with (
            patch.dict(
                s4_capacity_graph.TOOL_REGISTRY,
                {"analyze_s4_capacity_load": lambda **_: capacity_result},
            ),
            patch.object(
                s4_capacity_graph,
                "recommend_s4_routing_alternates",
                return_value=alternate_result,
            ) as recommend,
        ):
            result = s4_capacity_graph._tool_node({"messages": messages, "insights": []})

        recommend.assert_called_once_with(
            plant="PL01",
            work_center="WCEN009",
            order_id="000001001821",
            operation="0020",
            material="3000250",
            evaluation_date="2026-11-17",
            required_capacity=8.0,
            required_unit="H",
        )
        self.assertEqual(
            result["insights"][0]["data"]["alternate_assessments"][0]["alternates"][0]["work_center"],
            "WCEN005",
        )

    def test_alternate_checks_do_not_double_book_same_day_capacity(self):
        capacity_result = {
            "capacity_evaluations": [{
                "date": "2026-11-17",
                "plant": "PL01",
                "work_center": "WCEN009",
                "status": "overloaded",
            }],
            "contributors": [
                {
                    "date": "2026-11-17",
                    "plant": "PL01",
                    "work_center": "WCEN009",
                    "order_id": order_id,
                    "operation": "0020",
                    "material": "3000250",
                    "required_capacity": 8.0,
                    "capacity_unit": "H",
                }
                for order_id in ("000001001820", "000001001821")
            ],
        }

        def find_eight_hour_alternate(**_):
            return {
                "status": "completed",
                "operation_required_capacity": 8.0,
                "operation_required_capacity_unit": "HR",
                "alternates": [{
                    "work_center": "WCEN005",
                    "plant": "PL01",
                    "capacity_status": "within_capacity",
                    "sufficient_for_operation": True,
                    "remaining_after_move": 0.0,
                }],
            }

        with patch.object(
            s4_capacity_graph,
            "recommend_s4_routing_alternates",
            side_effect=find_eight_hour_alternate,
        ):
            assessments = s4_capacity_graph._assess_overloaded_alternates(capacity_result)

        first_alternate = assessments[0]["alternates"][0]
        second_alternate = assessments[1]["alternates"][0]
        self.assertTrue(first_alternate["sufficient_for_operation"])
        self.assertFalse(second_alternate["sufficient_for_operation"])
        self.assertTrue(second_alternate["capacity_conflict"])
        self.assertEqual(second_alternate["remaining_after_move"], -8.0)

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