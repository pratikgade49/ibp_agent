import unittest
from unittest.mock import patch

from agent import tools


class CapacityBottleneckTests(unittest.TestCase):
    @patch.object(tools, "USE_MOCK_DATA", False)
    @patch.object(tools, "_ibp_get")
    def test_production_aggregates_activity_with_resource_level_supply(self, ibp_get):
        ibp_get.side_effect = [
            {
                "d": {
                    "results": [
                        {
                            "RESID": "TEST-MACHINE",
                            "LOCID": "TEST-PLANT",
                            "PERIODID3_TSTAMP": "2026-10-01T00:00:00",
                            "CAPASUPPLY": 2000,
                        }
                    ]
                }
            },
            {
                "d": {
                    "results": [
                        {
                            "RESID": "TEST-MACHINE",
                            "LOCID": "TEST-PLANT",
                            "PRDID": "PRODUCT-1",
                            "SOURCEID": source,
                            "PERIODID3_TSTAMP": "2026-10-01T00:00:00",
                            "PCAPADEMAND": demand,
                            "PCAPAUSAGE": demand,
                            "CAPASUPPLY": 0,
                        }
                        for source, demand in (("SOURCE-1", 60), ("SOURCE-2", 40))
                    ]
                }
            },
        ]

        result = tools.analyze_capacity_bottlenecks(
            resource_type="production", product="PRODUCT-1"
        )

        self.assertEqual(len(result["results"]), 1)
        row = result["results"][0]
        self.assertEqual(row["source_ids"], ["SOURCE-1", "SOURCE-2"])
        self.assertEqual(row["source"], "SOURCE-1, SOURCE-2")
        self.assertEqual(row["demand"], 100)
        self.assertEqual(row["usage"], 100)
        self.assertEqual(row["supply"], 2000)
        self.assertEqual(row["utilization_pct"], 5)

        supply_query = ibp_get.call_args_list[0].kwargs
        self.assertNotIn("SOURCEID", supply_query["select"])
        self.assertNotIn("PRDID eq", supply_query["filter_"])


if __name__ == "__main__":
    unittest.main()