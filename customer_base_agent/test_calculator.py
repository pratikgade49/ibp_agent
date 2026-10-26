import unittest

from customer_base_agent.calculator import (
    ForecastInputError,
    calculate_customer_base_forecast,
)


class CustomerBaseForecastTests(unittest.TestCase):
    def test_existing_forecast_seeds_recursive_calculation(self):
        rows = [
            {"period": "2026-10", "base_forecast": 1000},
            {
                "period": "2026-11",
                "base_forecast": None,
                "ncs": 100,
                "attrition_rate": 0.12,
                "net_conversion": 50,
            },
            {
                "period": "2026-12",
                "base_forecast": None,
                "ncs": 100,
                "attrition_rate": 0.12,
                "net_conversion": 50,
            },
        ]

        result = calculate_customer_base_forecast(
            rows,
            seed_period="2026-10",
            start_period="2026-10",
            end_period="2026-12",
        )

        self.assertEqual(
            result,
            [
                {"period": "2026-10", "customer_base_forecast": 1000.0},
                {"period": "2026-11", "customer_base_forecast": 1140.0},
                {"period": "2026-12", "customer_base_forecast": 1278.6},
            ],
        )

    def test_matches_sample_future_periods_from_september_forecast(self):
        result = calculate_customer_base_forecast(
            [
                {"period": "2026-09", "base_forecast": 9400},
                {
                    "period": "2026-10",
                    "base_forecast": None,
                    "ncs": 550,
                    "attrition_rate": 0.1,
                    "net_conversion": 780,
                },
                {
                    "period": "2026-11",
                    "base_forecast": None,
                    "ncs": 600,
                    "attrition_rate": 0.1,
                    "net_conversion": 800,
                },
            ],
            seed_period="2026-09",
            start_period="2026-10",
            end_period="2026-11",
        )

        self.assertEqual(
            [row["customer_base_forecast"] for row in result],
            [10651.67, 11962.9],
        )

    def test_negative_net_conversion_reduces_forecast(self):
        result = calculate_customer_base_forecast(
            [
                {"period": "2026-10", "base_forecast": 1000},
                {
                    "period": "2026-11",
                    "base_forecast": None,
                    "ncs": 100,
                    "attrition_rate": 0,
                    "net_conversion": -250,
                },
            ],
            seed_period="2026-10",
            start_period="2026-10",
            end_period="2026-11",
        )

        self.assertEqual(
            [row["customer_base_forecast"] for row in result],
            [1000.0, 850.0],
        )

    def test_rejects_missing_future_period(self):
        with self.assertRaisesRegex(ForecastInputError, "Missing input row for 2026-11"):
            calculate_customer_base_forecast(
                [{"period": "2026-10", "base_forecast": 1000}],
                seed_period="2026-10",
                start_period="2026-10",
                end_period="2026-11",
            )


if __name__ == "__main__":
    unittest.main()