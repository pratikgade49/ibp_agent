import unittest
from unittest.mock import Mock, patch

from agent import tools


class MasterDataHealthCheckTests(unittest.TestCase):
    def test_mock_health_check_runs_local_quality_checks(self):
        result = tools.run_master_data_health_check(
            master_data_type="LOCATIONPRODUCT",
            planning_area="ZJPIBP1",
            filters=["LOCID eq '1010'"],
            attributes=["LOCID", "PRDID", "PRODUCTGROUP"],
            required_attributes=["PRODUCTGROUP"],
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["master_data_type"], "LOCATIONPRODUCT")
        self.assertEqual(result["result_scope"], "filtered")
        self.assertEqual(result["total_rows_evaluated"], 3)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["warning_count"], 0)
        self.assertEqual(result["source"], "mock")
        self.assertEqual(result["results"][0]["code"], "MISSING_REQUIRED_ATTRIBUTE")
        self.assertEqual(result["results"][0]["record"]["PRDID"], "Product A")

    @patch.object(tools, "USE_MOCK_DATA", False)
    @patch.object(tools, "IBP_BASE_URL", "https://ibp.example")
    @patch.object(tools, "IBP_USER", "planner")
    @patch.object(tools, "IBP_PASSWORD", "secret")
    @patch.object(tools.requests, "get")
    def test_live_health_check_extracts_from_master_data_service(
        self, get_response
    ):
        get_response.return_value = Mock(
            ok=True,
            json=Mock(
                return_value={
                    "d": {
                        "results": [
                            {"LOCID": "1010", "PRDID": "Product A", "PRODUCTGROUP": ""}
                        ]
                    }
                }
            ),
        )

        result = tools.run_master_data_health_check(
            master_data_type="LOCATIONPRODUCT",
            planning_area="ZJPIBP1",
            version="2026",
            filters=["LOCID eq '1010'"],
            attributes=["LOCID", "PRDID", "PRODUCTGROUP"],
            required_attributes=["PRODUCTGROUP"],
            max_results=25,
        )

        get_response.assert_called_once_with(
            "https://ibp.example/sap/opu/odata/IBP/MASTER_DATA_API_SRV/LOCATIONPRODUCT",
            params={
                "$select": "LOCID,PRDID,PRODUCTGROUP",
                "$top": 25,
                "$format": "json",
                "$filter": "LOCID eq '1010' and PlanningAreaID eq 'ZJPIBP1' and VersionID eq '2026'",
            },
            auth=("planner", "secret"),
            timeout=60,
        )
        self.assertEqual(result["source"], "live")
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["total_rows_evaluated"], 1)

    def test_health_check_rejects_invalid_filter_scope(self):
        with self.assertRaisesRegex(ValueError, "master_data_type"):
            tools.run_master_data_health_check(master_data_type="")

    def test_health_check_rejects_invalid_max_results(self):
        with self.assertRaisesRegex(ValueError, "max_results"):
            tools.run_master_data_health_check(
                master_data_type="LOCATIONPRODUCT",
                max_results=1001,
            )

    @patch.object(tools, "USE_MOCK_DATA", False)
    @patch.object(tools, "IBP_BASE_URL", "")
    def test_live_health_check_requires_destination_url(self):
        with self.assertRaisesRegex(RuntimeError, "IBP_BASE_URL"):
            tools.run_master_data_health_check(master_data_type="LOCATIONPRODUCT")

    @patch.object(tools, "USE_MOCK_DATA", False)
    @patch.object(tools, "IBP_BASE_URL", "https://ibp.example")
    @patch.object(tools, "IBP_USER", "planner")
    @patch.object(tools, "IBP_PASSWORD", "secret")
    @patch.object(tools, "MASTER_DATA_TYPE_PREFIX", "ZJP")
    @patch.object(tools.requests, "get")
    def test_product_health_check_discovers_related_master_data_types(
        self, get_response
    ):
        metadata = """<?xml version="1.0"?>
        <edmx:Edm xmlns:edmx="http://docs.oasis-open.org/odata/ns/edm">
          <edmx:EntityType Name="ZJPPRODUCT">
            <edmx:Property Name="PRDID" Type="Edm.String"/>
            <edmx:Property Name="PRODUCTGROUP" Type="Edm.String"/>
          </edmx:EntityType>
          <edmx:EntityType Name="ZJPLOCATION">
            <edmx:Property Name="PRDID" Type="Edm.String"/>
            <edmx:Property Name="LOCID" Type="Edm.String"/>
          </edmx:EntityType>
          <edmx:EntityType Name="ZJPPRODUCTTEXT">
            <edmx:Property Name="LANGUAGE" Type="Edm.String"/>
            <edmx:Property Name="TEXT" Type="Edm.String"/>
          </edmx:EntityType>
          <edmx:EntitySet Name="ZJPPRODUCT" EntityType="ZJPPRODUCT"/>
          <edmx:EntitySet Name="ZJPLOCATION" EntityType="ZJPLOCATION"/>
          <edmx:EntitySet Name="ZJPPRODUCTTEXT" EntityType="ZJPPRODUCTTEXT"/>
        </edmx:Edm>"""
        metadata_response = Mock(ok=True)
        metadata_response.text = metadata
        get_response.side_effect = [metadata_response, Mock(ok=True, json=Mock(return_value={"d": {"results": []}})), Mock(ok=True, json=Mock(return_value={"d": {"results": []}}))]

        result = tools.run_product_master_data_health_check(
            product="TestFG01",
            planning_area="ZJPIBP1",
            required_attributes=["PRDID", "PRODUCTGROUP"],
            max_results=25,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["product"], "TestFG01")
        self.assertEqual(
            [item["master_data_type"] for item in result["master_data_results"]],
            ["ZJPLOCATION", "ZJPPRODUCT"],
        )
        self.assertEqual(result["total_master_data_types"], 2)
        self.assertEqual(result["total_rows_evaluated"], 0)
        self.assertEqual(get_response.call_count, 3)
        self.assertEqual(
            get_response.call_args_list[1].kwargs["params"]["$filter"],
            "PRDID eq 'TestFG01' and PlanningAreaID eq 'ZJPIBP1'",
        )

    @patch.object(tools, "USE_MOCK_DATA", False)
    @patch.object(tools, "IBP_BASE_URL", "https://ibp.example")
    @patch.object(tools, "IBP_USER", "planner")
    @patch.object(tools, "IBP_PASSWORD", "secret")
    @patch.object(tools, "MASTER_DATA_TYPE_PREFIX", "ZJP")
    @patch.object(tools.requests, "get")
    def test_product_health_check_parses_sap_gateway_collections(
        self, get_response
    ):
        metadata = """<?xml version="1.0"?>
        <app:service xmlns:app="http://www.w3.org/2007/app"
            xmlns:atom="http://www.w3.org/2005/Atom">
          <app:workspace>
            <app:collection href="ZJPLOCATIONPRODUCT">
              <atom:title>ZJPLOCATIONPRODUCT</atom:title>
            </app:collection>
            <app:collection href="ZJPPRODUCT">
              <atom:title>ZJPPRODUCT</atom:title>
            </app:collection>
            <app:collection href="ZJPLOCATIONPRODUCTTrans">
              <atom:title>ZJPLOCATIONPRODUCTTrans</atom:title>
            </app:collection>
            <app:collection href="ZJPLOCATIONPRODUCT_VI">
              <atom:title>ZJPLOCATIONPRODUCT_VI</atom:title>
            </app:collection>
          </app:workspace>
        </app:service>"""
        metadata_response = Mock(ok=True, text=metadata)
        get_response.return_value = metadata_response

        result = tools._discover_product_master_data_types("ZJPIBP1")

        self.assertEqual(result, ["ZJPLOCATIONPRODUCT", "ZJPPRODUCT"])
        self.assertEqual(get_response.call_count, 1)


if __name__ == "__main__":
    unittest.main()
