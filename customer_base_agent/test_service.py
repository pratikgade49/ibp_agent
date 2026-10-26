import unittest

from customer_base_agent.config import CustomerBaseConfig
from customer_base_agent.service import commit_forecast, prepare_forecast


class FakeConnector:
    def __init__(self):
        self.read_request = None
        self.import_request = None

    def read_inputs(self, **kwargs):
        self.read_request = kwargs
        return [
            {
                "PRDFAMILY": "F1",
                "CATEGORY": "C1",
                "CUSTCOUNTRY": "US",
                "PATIENTTYPE": "P1",
                "period": "2026-09",
                "base_forecast": 1000,
                "ncs": 10,
                "attrition_rate": 0.12,
                "net_conversion": 20,
            },
            {
                "PRDFAMILY": "F1",
                "CATEGORY": "C1",
                "CUSTCOUNTRY": "US",
                "PATIENTTYPE": "P1",
                "period": "2026-10",
                "base_forecast": None,
                "ncs": 10,
                "attrition_rate": 0.12,
                "net_conversion": 20,
            },
            {
                "PRDFAMILY": "F1",
                "CATEGORY": "C1",
                "CUSTCOUNTRY": "US",
                "PATIENTTYPE": "P1",
                "period": "2026-11",
                "base_forecast": None,
                "ncs": 10,
                "attrition_rate": 0.12,
                "net_conversion": 20,
            },
        ]

    def import_forecasts(self, **kwargs):
        self.import_request = kwargs
        return {"row_count": len(kwargs["rows"])}


class FakeStore:
    def __init__(self):
        self.create_args = None
        self.run = None
        self.status = "prepared"

    def create(self, *args):
        self.create_args = args
        self.run = {
            "run_id": "stored-run-uuid",
            "planning_version_id": args[0],
            "scenario_id": args[1],
            "requester": args[2],
            "request": args[3],
            "status": self.status,
        }
        return {"run_id": "test-run"}

    def prepared_runs_for_requester(self, requester):
        if self.run and self.run["requester"] == requester and self.status == "prepared":
            return [self.run]
        return []

    def transition(self, run_id, requester, expected_status, status, **kwargs):
        self.status = status


class BaselinePreparationTests(unittest.TestCase):
    def test_baseline_request_uses_empty_version_id_and_preserves_scenario(self):
        connector = FakeConnector()
        read_inputs = connector.read_inputs

        def include_other_country(**kwargs):
            rows = read_inputs(**kwargs)
            rows.extend({**row, "CUSTCOUNTRY": "PL"} for row in list(rows))
            return rows

        connector.read_inputs = include_other_country
        store = FakeStore()
        config = CustomerBaseConfig(
            base_url="https://ibp.example",
            user="test-user",
            password="test-password",
            planning_area="INSPROD",
            period_level="3",
            dimensions=("PRDFAMILY", "CATEGORY", "CUSTCOUNTRY", "PATIENTTYPE"),
            key_figures={
                "ncs": "ZINSNCSTYPE",
                "attrition_rate": "ZINSANNUALATTRITIONRATE",
                "net_conversion": "ZINSNETCONVERSIONSTYPE",
                "seed_forecast": "ZINSCUSTOMERBASEFCSTTYPE",
                "forecast": "ZINSCOPYCUSTOMERBASEFORECAST",
            },
            navigation_property="NavINSPROD",
            transaction_name="Customer Base Forecast Agent",
            timeout_seconds=10,
            database_path=":memory:",
        )

        preview = prepare_forecast(
            planning_version_id="Baseline",
            scenario_id="FA163EB181801FD19AC7A2D6109D8E66",
            requester="test-planner",
            start_period="2026-10",
            end_period="2026-11",
            country="US",
            config=config,
            connector=connector,
            store=store,
        )

        self.assertEqual(connector.read_request["planning_version_id"], "")
        self.assertEqual(
            connector.read_request["scenario_id"],
            "FA163EB181801FD19AC7A2D6109D8E66",
        )
        self.assertEqual(preview["planning_version_id"], "")
        self.assertEqual(preview["scenario_id"], "FA163EB181801FD19AC7A2D6109D8E66")
        self.assertEqual(preview["country"], "US")
        self.assertEqual(connector.read_request["country"], "US")
        self.assertEqual(
            [row["customer_base_forecast"] for row in preview["forecast_rows"]],
            [1020.0, 1039.8],
        )
        self.assertEqual(
            {row["CUSTCOUNTRY"] for row in preview["forecast_rows"]},
            {"US"},
        )

    def test_percent_attrition_value_is_normalized_before_forecast(self):
        connector = FakeConnector()
        connector.read_inputs = lambda **kwargs: [
            {
                "PRDFAMILY": "F1",
                "CATEGORY": "C1",
                "CUSTCOUNTRY": "US",
                "PATIENTTYPE": "P1",
                "period": "2026-09",
                "base_forecast": 1000,
                "ncs": 10,
                "attrition_rate": 8.5,
                "net_conversion": 20,
            },
            {
                "PRDFAMILY": "F1",
                "CATEGORY": "C1",
                "CUSTCOUNTRY": "US",
                "PATIENTTYPE": "P1",
                "period": "2026-10",
                "base_forecast": None,
                "ncs": 10,
                "attrition_rate": 8.5,
                "net_conversion": 20,
            },
            {
                "PRDFAMILY": "F1",
                "CATEGORY": "C1",
                "CUSTCOUNTRY": "US",
                "PATIENTTYPE": "P1",
                "period": "2026-11",
                "base_forecast": None,
                "ncs": 10,
                "attrition_rate": 8.5,
                "net_conversion": 20,
            },
        ]
        config = CustomerBaseConfig(
            base_url="https://ibp.example",
            user="test-user",
            password="test-password",
            planning_area="INSPROD",
            period_level="3",
            dimensions=("PRDFAMILY", "CATEGORY", "CUSTCOUNTRY", "PATIENTTYPE"),
            key_figures={
                "ncs": "ZINSNCSTYPE",
                "attrition_rate": "ZINSANNUALATTRITIONRATE",
                "net_conversion": "ZINSNETCONVERSIONSTYPE",
                "seed_forecast": "ZINSCUSTOMERBASEFCSTTYPE",
                "forecast": "ZINSCOPYCUSTOMERBASEFORECAST",
            },
            navigation_property="NavINSPROD",
            transaction_name="Customer Base Forecast Agent",
            timeout_seconds=10,
            database_path=":memory:",
        )

        preview = prepare_forecast(
            planning_version_id="Baseline",
            scenario_id="FA163EB181801FD19AC7A2D6109D8E66",
            requester="test-planner",
            start_period="2026-10",
            end_period="2026-11",
            config=config,
            connector=connector,
            store=FakeStore(),
        )

        self.assertEqual(
            [row["customer_base_forecast"] for row in preview["forecast_rows"]],
            [1022.92, 1045.67],
        )

    def test_approval_phrase_resolves_prepared_run_without_run_id_input(self):
        connector = FakeConnector()
        store = FakeStore()
        config = CustomerBaseConfig(
            base_url="https://ibp.example",
            user="test-user",
            password="test-password",
            planning_area="INSPROD",
            period_level="3",
            dimensions=("PRDFAMILY", "CATEGORY", "CUSTCOUNTRY", "PATIENTTYPE"),
            key_figures={
                "ncs": "ZINSNCSTYPE",
                "attrition_rate": "ZINSANNUALATTRITIONRATE",
                "net_conversion": "ZINSNETCONVERSIONSTYPE",
                "seed_forecast": "ZINSCUSTOMERBASEFCSTTYPE",
                "forecast": "ZINSCOPYCUSTOMERBASEFORECAST",
            },
            navigation_property="NavINSPROD",
            transaction_name="Customer Base Forecast Agent",
            timeout_seconds=10,
            database_path=":memory:",
        )
        preview = prepare_forecast(
            planning_version_id="Baseline",
            scenario_id="SCENARIO",
            requester="test-planner",
            start_period="2026-10",
            end_period="2026-10",
            config=config,
            connector=connector,
            store=store,
        )

        result = commit_forecast(
            requester="test-planner",
            approval_phrase=preview["approval_phrase"],
            user_message=preview["approval_phrase"],
            config=config,
            connector=connector,
            store=store,
        )

        self.assertEqual(result["status"], "import_submitted")
        self.assertEqual(result["run_id"], "stored-run-uuid")
        self.assertEqual(connector.import_request["rows"][0]["period"], "2026-10")


if __name__ == "__main__":
    unittest.main()