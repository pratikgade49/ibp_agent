import os
import json
import unittest
from unittest.mock import patch

from customer_base_agent.config import CustomerBaseConfig


class CustomerBaseConfigTests(unittest.TestCase):
    def test_reads_credentials_from_bound_user_provided_service(self):
        service_bindings = {
            "user-provided": [
                {
                    "name": "customer-base-ibp-credentials",
                    "label": "user-provided",
                    "credentials": {
                        "base_url": "https://bound.example/",
                        "username": "bound-user",
                        "password": "bound-password",
                    },
                }
            ]
        }
        with patch.dict(
            os.environ,
            {
                "VCAP_SERVICES": json.dumps(service_bindings),
                "CUSTOMER_BASE_IBP_BASE_URL": "https://fallback.example",
                "CUSTOMER_BASE_IBP_USER": "fallback-user",
                "CUSTOMER_BASE_IBP_PASSWORD": "fallback-password",
            },
            clear=True,
        ):
            config = CustomerBaseConfig.from_env()

        self.assertEqual(config.base_url, "https://bound.example")
        self.assertEqual(config.user, "bound-user")
        self.assertEqual(config.password, "bound-password")

    def test_uses_separate_scenario_connection_settings(self):
        values = {
            "IBP_BASE_URL": "https://shared.example",
            "IBP_USER": "shared-user",
            "IBP_PASSWORD": "shared-password",
            "CUSTOMER_BASE_IBP_BASE_URL": "https://scenario.example/",
            "CUSTOMER_BASE_IBP_USER": "scenario-user",
            "CUSTOMER_BASE_IBP_PASSWORD": "scenario-password",
        }
        with patch.dict(os.environ, values, clear=True):
            config = CustomerBaseConfig.from_env()

        self.assertEqual(config.base_url, "https://scenario.example")
        self.assertEqual(config.user, "scenario-user")
        self.assertEqual(config.password, "scenario-password")
        self.assertEqual(config.planning_area, "INSPROD")
        self.assertEqual(config.navigation_property, "NavINSPROD")

    def test_does_not_fallback_to_existing_ibp_credentials(self):
        values = {
            "IBP_BASE_URL": "https://shared.example",
            "IBP_USER": "shared-user",
            "IBP_PASSWORD": "shared-password",
        }
        with patch.dict(os.environ, values, clear=True):
            config = CustomerBaseConfig.from_env()

        with self.assertRaisesRegex(RuntimeError, "CUSTOMER_BASE_IBP_BASE_URL"):
            config.validate_connection()


if __name__ == "__main__":
    unittest.main()