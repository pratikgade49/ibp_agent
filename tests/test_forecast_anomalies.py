import unittest
from unittest.mock import Mock, patch

from agent import tools


class ForecastAnomalyTests(unittest.TestCase):
    @patch.object(tools, "IBP_BASE_URL", "https://ibp.example")
    @patch.object(tools, "IBP_USER", "reader")
    @patch.object(tools, "IBP_PASSWORD", "secret")
    def test_ibp_get_collects_all_odata_pages(self):
        first_page = Mock(
            ok=True,
            json=Mock(
                return_value={
                    "d": {
                        "results": [{"PRDID": "P1"}],
                        "__next": "/planning?$skiptoken=next-page",
                    }
                }
            ),
        )
        second_page = Mock(
            ok=True,
            json=Mock(return_value={"d": {"results": [{"PRDID": "P2"}]}}),
        )
        with patch.object(
            tools.requests,
            "get",
            side_effect=[first_page, second_page],
        ) as get:
            result = tools._ibp_get("PRDID", "UOMTOID eq 'EA'")

        self.assertEqual(result["d"]["results"], [{"PRDID": "P1"}, {"PRDID": "P2"}])
        self.assertNotIn("__next", result["d"])
        self.assertEqual(
            get.call_args_list[1].args[0],
            "https://ibp.example/planning?$skiptoken=next-page",
        )

    def test_all_products_ignores_product_filter_and_aggregates_by_product(self):
        result = tools.detect_forecast_anomalies(
            product="B4",
            all_products=True,
        )

        self.assertIsNone(result["product_filter"])
        self.assertEqual(result["result_scope"], "product")
        self.assertEqual(
            {anomaly["product_id"] for anomaly in result["anomalies"]},
            {"B1", "B4"},
        )


if __name__ == "__main__":
    unittest.main()