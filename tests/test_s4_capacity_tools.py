import unittest
from datetime import date, timedelta
from unittest.mock import Mock, patch

from agent import s4_capacity_tools as tools


class S4CapacityReaderTests(unittest.TestCase):
    @patch.object(tools, "S4_BASE_URL", "https://s4.example")
    @patch.object(tools, "S4_USER", "reader")
    @patch.object(tools, "S4_PASSWORD", "secret")
    def test_defaults_missing_dates_to_next_30_calendar_days(self):
        with patch.object(
            tools,
            "_s4_get",
            side_effect=[
                {"d": {"results": []}},
                {"d": {"results": []}},
            ],
        ) as get:
            result = tools.analyze_s4_capacity_load()

        expected_start = date.today()
        expected_end = expected_start + timedelta(days=29)
        self.assertEqual(
            result["evaluation"],
            {
                "start_date": expected_start.isoformat(),
                "end_date": expected_end.isoformat(),
                "bucket_type": "D",
                "date_range_defaulted": True,
            },
        )
        self.assertIn(
            f"P_CapEvalStartDate=datetime'{expected_start.isoformat()}T00:00:00'",
            get.call_args_list[0].args[0],
        )
        self.assertIn(
            f"P_CapEvalEndDate=datetime'{expected_end.isoformat()}T00:00:00'",
            get.call_args_list[0].args[0],
        )

    def test_reads_all_work_centers_when_unscoped(self):
        work_centers = [
            {"Plant": "PL01", "WorkCenter": "WCEN001"},
            {"Plant": "PL02", "WorkCenter": "WCEN002"},
        ]
        with patch.object(
            tools,
            "_s4_get",
            return_value={"d": {"results": work_centers}},
        ) as get:
            result = tools.read_s4_work_center_capacities()

        self.assertEqual(result["work_center_count"], 2)
        self.assertEqual(
            [(row["plant"], row["work_center"]) for row in result["work_centers"]],
            [("PL01", "WCEN001"), ("PL02", "WCEN002")],
        )
        self.assertNotIn("$filter", get.call_args.args[1])

    @patch.object(tools, "S4_BASE_URL", "https://s4.example")
    @patch.object(tools, "S4_USER", "reader")
    @patch.object(tools, "S4_PASSWORD", "secret")
    def test_evaluates_all_plants_and_work_centers_when_unscoped(self):
        first_bucket = {
            "Plant": "PL01",
            "WorkCenter": "WCEN003",
            "CapacityEvaluationTimePeriod": "20261023",
            "WorkCenterCapRqmtInCapUnit": "16.00",
            "WorkCenterAvailableCapacity": "8.00",
            "WrkCtrRmngCapInCapUnit": "-8.00",
            "WorkCenterCapacityUnit": "H",
        }
        second_bucket = {
            "Plant": "PL02",
            "WorkCenter": "WCEN004",
            "CapacityEvaluationTimePeriod": "20261024",
            "WorkCenterCapRqmtInCapUnit": "4.00",
            "WorkCenterAvailableCapacity": "8.00",
            "WrkCtrRmngCapInCapUnit": "4.00",
            "WorkCenterCapacityUnit": "H",
        }
        with patch.object(
            tools,
            "_s4_get",
            side_effect=[
                {"d": {"results": [first_bucket, second_bucket]}},
                {"d": {"results": []}},
            ],
        ) as get:
            result = tools.analyze_s4_capacity_load(
                start_date="2026-10-13", end_date="2026-12-12"
            )

        self.assertEqual(result["evaluation_count"], 2)
        self.assertEqual(
            {(row["plant"], row["work_center"]) for row in result["capacity_evaluations"]},
            {("PL01", "WCEN003"), ("PL02", "WCEN004")},
        )
        self.assertEqual(result["capacity_evaluations"][0]["status"], "overloaded")
        self.assertEqual(get.call_args_list[0].args[1], {"$format": "json"})
        self.assertEqual(get.call_args_list[1].args[1], {"$format": "json"})

    @patch.object(tools, "S4_BASE_URL", "https://s4.example")
    @patch.object(tools, "S4_USER", "reader")
    @patch.object(tools, "S4_PASSWORD", "secret")
    def test_s4_get_collects_all_odata_pages(self):
        page_one = Mock(
            ok=True,
            json=lambda: {
                "d": {
                    "results": [{"WorkCenter": "WCEN001"}],
                    "__next": "https://s4.example/next-page",
                }
            },
        )
        page_two = Mock(
            ok=True,
            json=lambda: {"d": {"results": [{"WorkCenter": "WCEN002"}]}},
        )
        with patch.object(tools.requests, "get", side_effect=[page_one, page_two]) as get:
            payload = tools._s4_get("A_WorkCenters", {"$format": "json"})

        self.assertEqual(
            [row["WorkCenter"] for row in payload["d"]["results"]],
            ["WCEN001", "WCEN002"],
        )
        self.assertNotIn("__next", payload["d"])
        self.assertEqual(get.call_count, 2)

    @patch.object(tools, "S4_BASE_URL", "https://s4.example")
    @patch.object(tools, "S4_USER", "reader")
    @patch.object(tools, "S4_PASSWORD", "secret")
    def test_reads_work_center_capacity_and_weekday_shift(self):
        work_center = {
            "WorkCenterInternalID": "10000294",
            "WorkCenterTypeCode": "A",
            "WorkCenter": "RESOR_01",
            "WorkCenterDesc": "Process Res 01",
            "Plant": "PL01",
            "WorkCenterCategoryCode": "0008",
            "CapacityInternalID": "10000288",
            "ValidityStartDate": "/Date(1789689600000)/",
            "ValidityEndDate": "/Date(253402214400000)/",
        }
        capacity = {
            "CapacityInternalID": "10000288",
            "CapacityCategoryCode": "001",
            "CapacityActiveVersion": "3",
            "CapacityIsFinite": False,
            "CapacityNumberOfCapacities": 1,
            "CapacityPlanUtilizationPercent": "100",
            "CapacityBreakDuration": 0,
            "FactoryCalendar": "IN",
            "CapacityStartTime": 36000,
            "CapacityEndTime": 64800,
            "CapacityQuantityUnit": "HR",
            "CapacityBaseQtyUnit": "HR",
        }
        shift = {
            "WeekDay": "1",
            "IntervalStartDate": "/Date(1789689600000)/",
            "IntervalEndDate": "/Date(253402214400000)/",
            "ShiftStartTime": "PT10H00M00S",
            "ShiftEndTime": "PT18H00M00S",
            "CapacityBreakDuration": 0,
            "CapacityPlanUtilizationPercent": "100",
            "CapacityNumberOfCapacities": 1,
            "TotOperatingDurationInHours": "8.000",
            "TotOperationDurationUnit": "H",
        }

        def fake_get(url, params=None, **kwargs):
            if url.endswith("/A_WorkCenters"):
                return Mock(ok=True, json=lambda: {"d": {"results": [work_center]}})
            if url.endswith("/A_WorkCenterCapacity('10000288')"):
                return Mock(ok=True, json=lambda: {"d": capacity})
            if url.endswith("/A_WorkCenterCapacityShift"):
                return Mock(ok=True, json=lambda: {"d": {"results": [shift]}})
            self.fail(f"Unexpected request URL: {url}")

        with patch.object(tools.requests, "get", side_effect=fake_get) as get:
            result = tools.read_s4_work_center_capacities(work_center="RESOR_01")

        self.assertEqual(result["work_center_count"], 1)
        self.assertFalse(result["work_center_list_truncated"])
        row = result["work_centers"][0]
        self.assertEqual(row["work_center"], "RESOR_01")
        self.assertEqual(row["capacity"]["start_time"], "10:00:00")
        self.assertEqual(row["capacity"]["end_time"], "18:00:00")
        self.assertFalse(row["capacity"]["is_finite"])
        self.assertEqual(row["shifts"][0]["operating_hours"], "8.000")
        self.assertEqual(row["shifts"][0]["weekday_code"], "1")
        self.assertEqual(get.call_count, 3)
        shift_call = get.call_args_list[2]
        self.assertIn("CapacityActiveVersion eq '3'", shift_call.kwargs["params"]["$filter"])

    @patch.object(tools, "S4_BASE_URL", "https://s4.example")
    @patch.object(tools, "S4_USER", "reader")
    @patch.object(tools, "S4_PASSWORD", "secret")
    def test_uses_sap_evaluation_for_overload_and_order_contributors(self):
        aggregate = {
            "P_CapEvalStartDate": "/Date(1792713600000)/",
            "P_CapEvalEndDate": "/Date(1792713600000)/",
            "P_CapEvalBucketType": "D",
            "Plant": "PL01",
            "WorkCenter": "WCEN003",
            "CapacityInternalID": "10000282",
            "ShiftName": "",
            "CapacityEvaluationTimePeriod": "20261023",
            "CapacityEvaluationTimePerdText": "Day",
            "WorkCenterAvailableCapacity": "8.00",
            "WorkCenterCapRqmtInCapUnit": "16.00",
            "WrkCtrRmngCapInCapUnit": "-8.00",
            "WorkCenterTotUtilznInTmePerd": "2E2",
            "WorkCenterCapacityUnit": "H",
        }
        contributors = [
            {
                "Plant": "PL01",
                "WorkCenter": "WCEN003",
                "OrderID": order_id,
                "Operation": "0010",
                "Material": "3000190",
                "OrderStatusText": "Created",
                "OperationPlanningStatusText": "Not Dispatched",
                "WorkCenterAvailableCapacity": "8.00",
                "WorkCenterCapRqmtInCapUnit": "8.00",
                "WorkCenterCapacityUnit": "HR",
            }
            for order_id in ("000001001800", "000001001785")
        ]
        get_responses = [
            {"d": {"results": [aggregate]}},
            {"d": {"results": contributors}},
        ]
        with patch.object(
            tools,
            "_s4_get",
            side_effect=get_responses,
        ) as get:
            result = tools.analyze_s4_capacity_load(
                "PL01", "WCEN003", "2026-10-23", "2026-10-23"
            )

        self.assertEqual(result["source"], "SAP evaluated capacity API")
        self.assertEqual(result["evaluation_count"], 1)
        self.assertEqual(result["evaluated_bucket_dates"], ["20261023"])
        evaluation = result["capacity_evaluations"][0]
        self.assertEqual(evaluation["required_capacity"], 16.0)
        self.assertEqual(evaluation["available_capacity"], 8.0)
        self.assertEqual(evaluation["remaining_capacity"], -8.0)
        self.assertEqual(evaluation["utilization_percent"], 200.0)
        self.assertEqual(evaluation["status"], "overloaded")
        self.assertEqual(result["contributor_count"], 2)
        self.assertEqual(
            {row["order_id"] for row in result["contributors"]},
            {"000001001800", "000001001785"},
        )

        aggregate_call, contributor_call = get.call_args_list
        self.assertIn("A_WorkCenterCapPerBucket(", aggregate_call.args[0])
        self.assertIn(")/Set", aggregate_call.args[0])
        self.assertEqual(aggregate_call.args[1]["$filter"], "Plant eq 'PL01' and WorkCenter eq 'WCEN003'")
        self.assertIn("A_WorkCenterCapOrderPerBucket(", contributor_call.args[0])
        self.assertIn("P_CapEvalBucketType='D'", contributor_call.args[0])

    def test_rejects_invalid_evaluation_date_range(self):
        with self.assertRaisesRegex(ValueError, "start_date must use YYYY-MM-DD"):
            tools.analyze_s4_capacity_load("PL01", "WCEN003", "20261023", "2026-10-23")

        with self.assertRaisesRegex(ValueError, "start_date must not be after end_date"):
            tools.analyze_s4_capacity_load("PL01", "WCEN003", "2026-10-24", "2026-10-23")


if __name__ == "__main__":
    unittest.main()