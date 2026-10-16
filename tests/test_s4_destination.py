import json
import os
import unittest
from unittest.mock import Mock, patch

from agent import s4_capacity_tools as tools


class S4DestinationTests(unittest.TestCase):
    def test_s4_get_uses_destination_and_connectivity_proxy(self):
        vcap_services = {
            "destination": [
                {
                    "label": "destination",
                    "name": "ibp-destination",
                    "credentials": {
                        "uri": "https://destination.example",
                        "url": "https://954653ebtrial.authentication.us10.hana.ondemand.com",
                        "uaadomain": "destination-uaa.example",
                        "clientid": "destination-client",
                        "clientsecret": "destination-secret",
                    },
                }
            ],
            "connectivity": [
                {
                    "label": "connectivity",
                    "name": "ibp-connectivity",
                    "credentials": {
                        "onpremise_proxy_host": "proxy.example",
                        "onpremise_proxy_port": "20003",
                        "token_service_url": "https://connectivity-uaa.example/oauth/token",
                        "clientid": "connectivity-client",
                        "clientsecret": "connectivity-secret",
                    },
                }
            ],
        }
        token_responses = [
            Mock(ok=True, json=lambda: {"access_token": "destination-token"}),
            Mock(ok=True, json=lambda: {"access_token": "connectivity-token"}),
        ]
        destination_response = Mock(
            ok=True,
            json=lambda: {
                "destinationConfiguration": {
                    "URL": "https://s4.virtual.example:44301",
                    "ProxyType": "OnPremise",
                    "Authentication": "BasicAuthentication",
                    "User": "s4-reader",
                    "Password": "s4-password",
                    "CloudConnectorLocationId": "cc-us10",
                }
            },
        )
        sap_response = Mock(ok=True, json=lambda: {"d": {"results": []}})

        with (
            patch.object(tools, "S4_DESTINATION_NAME", "S3DTEST"),
            patch.dict(os.environ, {"VCAP_SERVICES": json.dumps(vcap_services)}),
            patch.object(tools.requests, "post", side_effect=token_responses) as post,
            patch.object(
                tools.requests,
                "get",
                side_effect=[destination_response, sap_response],
            ) as get,
        ):
            result = tools._s4_get("A_WorkCenters", {"$format": "json"})

        self.assertEqual(result, {"d": {"results": []}})
        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            post.call_args_list[0].args[0],
            "https://954653ebtrial.authentication.us10.hana.ondemand.com/oauth/token",
        )
        self.assertIn(
            "/destination-configuration/v1/destinations/S3DTEST",
            get.call_args_list[0].args[0],
        )
        sap_call = get.call_args_list[1]
        self.assertEqual(
            sap_call.args[0],
            "https://s4.virtual.example:44301/sap/opu/odata/sap/API_WORK_CENTERS/A_WorkCenters",
        )
        self.assertEqual(sap_call.kwargs["auth"], ("s4-reader", "s4-password"))
        self.assertEqual(
            sap_call.kwargs["proxies"],
            {
                "http": "http://proxy.example:20003",
                "https": "http://proxy.example:20003",
            },
        )
        self.assertEqual(
            sap_call.kwargs["headers"]["Proxy-Authorization"],
            "Bearer connectivity-token",
        )
        self.assertEqual(
            sap_call.kwargs["headers"]["SAP-Connectivity-SCC-Location_ID"],
            "cc-us10",
        )

    def test_oauth_url_requires_configured_absolute_endpoint(self):
        with self.assertRaisesRegex(RuntimeError, "missing its OAuth URL"):
            tools._service_oauth_url({}, "Destination service")

        with self.assertRaisesRegex(RuntimeError, r"absolute HTTP\(S\) URL"):
            tools._oauth_token_endpoint("/oauth/token")


if __name__ == "__main__":
    unittest.main()