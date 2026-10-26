import os
import unittest
from unittest.mock import Mock, patch

from customer_base_agent.config import CustomerBaseConfig
from customer_base_agent.ibp_connector import IBPConnector


class IBPConnectorTests(unittest.TestCase):
    def test_reads_forecast_key_figure_as_seed_without_actuals(self):
        environment = {
            "CUSTOMER_BASE_IBP_BASE_URL": "https://ibp.example",
            "CUSTOMER_BASE_IBP_USER": "test-user",
            "CUSTOMER_BASE_IBP_PASSWORD": "test-password",
        }
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "d": {
                "results": [
                    {
                        "PRDFAMILY": "F1",
                        "CATEGORY": "C1",
                        "CUSTCOUNTRY": "US",
                        "PATIENTTYPE": "P1",
                        "PERIODID3_TSTAMP": "2026-09-01T00:00:00",
                        "ZINSNCSTYPE": "1",
                        "ZINSANNUALATTRITIONRATE": "0.1",
                        "ZINSNETCONVERSIONSTYPE": "2",
                        "ZINSCUSTOMERBASEFCSTTYPE": "9400",
                        "ZINSCOPYCUSTOMERBASEFORECAST": "8123",
                    }
                ]
            }
        }

        with patch.dict(os.environ, environment, clear=True):
            connector = IBPConnector(CustomerBaseConfig.from_env())
        with patch("customer_base_agent.ibp_connector.requests.request", return_value=response) as request:
            rows = connector.read_inputs(
                planning_version_id="PLAN_A",
                scenario_id="TEST_SCENARIO",
                start_period="2026-10",
                end_period="2027-05",
                seed_period="2026-09",
                country="FR",
            )

        selected_fields = request.call_args.kwargs["params"]["$select"].split(",")
        filter_expression = request.call_args.kwargs["params"]["$filter"]
        self.assertIn("PERIODID3_TSTAMP ge datetime'2026-09-01T00:00:00'", filter_expression)
        self.assertIn("ZINSCUSTOMERBASEFCSTTYPE", selected_fields)
        self.assertNotIn("ZINSCOPYCUSTOMERBASEFORECAST", selected_fields)
        self.assertNotIn("CUSTOMERBASEACTUALS", selected_fields)
        self.assertIn("VERSIONID eq 'PLAN_A'", filter_expression)
        self.assertIn("SCENARIOID eq 'TEST_SCENARIO'", filter_expression)
        self.assertIn("CUSTCOUNTRY eq 'FR'", filter_expression)
        self.assertEqual(rows[0]["base_forecast"], 9400.0)

    def test_baseline_read_uses_empty_version_filter(self):
        environment = {
            "CUSTOMER_BASE_IBP_BASE_URL": "https://ibp.example",
            "CUSTOMER_BASE_IBP_USER": "test-user",
            "CUSTOMER_BASE_IBP_PASSWORD": "test-password",
        }
        response = Mock(ok=True)
        response.json.return_value = {"d": {"results": []}}
        with patch.dict(os.environ, environment, clear=True):
            connector = IBPConnector(CustomerBaseConfig.from_env())
        with patch("customer_base_agent.ibp_connector.requests.request", return_value=response) as request:
            connector.read_inputs(
                planning_version_id="",
                scenario_id="TEST_SCENARIO",
                start_period="2026-10",
                end_period="2027-05",
                seed_period="2026-09",
            )

        filter_expression = request.call_args.kwargs["params"]["$filter"]
        self.assertIn("VERSIONID eq ''", filter_expression)
        self.assertIn("SCENARIOID eq 'TEST_SCENARIO'", filter_expression)

    def test_import_targets_scenario_using_trans_entity_scenarioid(self):
        environment = {
            "CUSTOMER_BASE_IBP_BASE_URL": "https://ibp.example",
            "CUSTOMER_BASE_IBP_USER": "test-user",
            "CUSTOMER_BASE_IBP_PASSWORD": "test-password",
        }
        token_response = Mock(ok=True, headers={"x-csrf-token": "test-token"})
        import_response = Mock(ok=True, content=b"")
        session = Mock()
        session.get.return_value = token_response
        session.post.return_value = import_response
        with patch.dict(os.environ, environment, clear=True):
            connector = IBPConnector(CustomerBaseConfig.from_env())
        with patch("customer_base_agent.ibp_connector.requests.Session", return_value=session):
            result = connector.import_forecasts(
                planning_version_id="PLAN_A",
                scenario_id="TEST_SCENARIO",
                rows=[{
                    "period": "2026-10",
                    "customer_base_forecast": 10651.67,
                    "PRDFAMILY": "F1",
                    "CATEGORY": "C1",
                    "CUSTCOUNTRY": "US",
                    "PATIENTTYPE": "P1",
                }],
            )

        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["VersionID"], "PLAN_A")
        self.assertEqual(payload["ScenarioID"], "TEST_SCENARIO")
        self.assertEqual(result["planning_version_id"], "PLAN_A")
        self.assertEqual(result["scenario_id"], "TEST_SCENARIO")

    def test_baseline_import_sends_empty_version_id(self):
        environment = {
            "CUSTOMER_BASE_IBP_BASE_URL": "https://ibp.example",
            "CUSTOMER_BASE_IBP_USER": "test-user",
            "CUSTOMER_BASE_IBP_PASSWORD": "test-password",
        }
        token_response = Mock(ok=True, headers={"x-csrf-token": "test-token"})
        session = Mock()
        session.get.return_value = token_response
        session.post.return_value = Mock(ok=True, content=b"")
        with patch.dict(os.environ, environment, clear=True):
            connector = IBPConnector(CustomerBaseConfig.from_env())
        with patch("customer_base_agent.ibp_connector.requests.Session", return_value=session):
            result = connector.import_forecasts(
                planning_version_id="",
                scenario_id="TEST_SCENARIO",
                rows=[{
                    "period": "2026-10",
                    "customer_base_forecast": 100,
                    "PRDFAMILY": "F1",
                    "CATEGORY": "C1",
                    "CUSTCOUNTRY": "US",
                    "PATIENTTYPE": "P1",
                }],
            )

        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["VersionID"], "")
        self.assertEqual(payload["ScenarioID"], "TEST_SCENARIO")
        self.assertEqual(result["planning_version_id"], "")


if __name__ == "__main__":
    unittest.main()