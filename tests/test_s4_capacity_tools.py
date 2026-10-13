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

    @patch.object(tools, "S4_BASE_URL", "https://s4.example")
    @patch.object(tools, "S4_USER", "reader")
    @patch.object(tools, "S4_PASSWORD", "secret")
    @patch.object(tools, "read_s4_work_center_capacities")
    def test_load_sums_operation_hours_by_scheduled_start_date(self, read_capacity):
        read_capacity.return_value = {
            "work_centers": [{
                "work_center": "RESOR_01",
                "capacity": {"number_of_capacities": 1},
                "shifts": [{"operating_hours": "8.000"}],
            }]
        }
        operations = [
            {
                "ManufacturingOrder": "1001782",
                "ProductionPlant": "PL01",
                "WorkCenter": "RESOR_01",
                "OpErlstSchedldExecStrtDte": "/Date(1792713600000)/",
                "WorkCenterStandardWorkQty2": "4.000",
                "StdWrkQtyUnit2SAPCode": "H",
            },
            {
                "ManufacturingOrder": "1001783",
                "ProductionPlant": "PL01",
                "WorkCenter": "RESOR_01",
                "OpErlstSchedldExecStrtDte": "/Date(1792713600000)/",
                "WorkCenterStandardWorkQty2": "4.000",
                "StdWrkQtyUnit2SAPCode": "H",
            },
            {
                "ManufacturingOrder": "1001784",
                "ProductionPlant": "PL01",
                "WorkCenter": "RESOR_01",
                "OpErlstSchedldExecStrtDte": "/Date(1792800000000)/",
                "WorkCenterStandardWorkQty2": "6.000",
                "StdWrkQtyUnit2SAPCode": "H",
            },
        ]
        with patch.object(
            tools,
            "_s4_get",
            return_value={"d": {"results": operations}},
        ) as get:
            result = tools.analyze_s4_capacity_load("PL01", "RESOR_01")

        self.assertEqual(result["daily_load"][0]["required_hours"], 8.0)
        self.assertEqual(result["daily_load"][0]["operation_count"], 2)
        self.assertEqual(result["daily_load"][0]["orders"], ["1001782", "1001783"])
        self.assertFalse(result["capacity_calendar_complete"])
        operation_call = get.call_args_list[0]
        self.assertIn("ProductionPlant eq 'PL01'", operation_call.args[1]["$filter"])
        self.assertEqual(operation_call.kwargs["service_root"], tools.S4_PRODUCTION_ORDER_SERVICE_ROOT)
        read_capacity.assert_called_once_with(plant="PL01", work_center="RESOR_01")

    def test_load_requires_plant_and_work_center(self):
        with self.assertRaisesRegex(ValueError, "plant and work_center are both required"):
            tools.analyze_s4_capacity_load("PL01", "")


if __name__ == "__main__":
    unittest.main()