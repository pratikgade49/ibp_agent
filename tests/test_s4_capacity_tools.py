import unittest
from unittest.mock import Mock, patch

from agent import s4_capacity_tools as tools


class S4CapacityReaderTests(unittest.TestCase):
    def test_requires_a_scope_filter(self):
        with self.assertRaisesRegex(ValueError, "provide a plant or work_center"):
            tools.read_s4_work_center_capacities()

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


if __name__ == "__main__":
    unittest.main()