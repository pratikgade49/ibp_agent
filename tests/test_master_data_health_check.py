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
    def test_product_discovery_parses_odata_v2_metadata(self, get_response):
        metadata = """<?xml version="1.0" encoding="utf-8"?>
        <edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
         <edmx:DataServices>
          <Schema Namespace="IBP.MASTER_DATA_API_SRV"
                  xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
           <EntityType Name="ZJPPRODUCT"><Property Name="PRDID"/></EntityType>
           <EntityType Name="ZJPPRODUCT_VI"><Property Name="PRDID"/></EntityType>
           <EntityType Name="ZJPPRODUCTTrans"><Property Name="TransactionID"/></EntityType>
           <EntityType Name="ZJPPRODUCTMessage"><Property Name="TransactionID"/></EntityType>
           <EntityType Name="ZJPLOCATIONPRODUCT"><Property Name="PRDID"/></EntityType>
           <EntityType Name="ZJPLOCATION"><Property Name="LOCID"/></EntityType>
           <EntityType Name="OTHERPRODUCT"><Property Name="PRDID"/></EntityType>
           <EntityContainer Name="C">
            <EntitySet Name="ZJPPRODUCT" EntityType="IBP.MASTER_DATA_API_SRV.ZJPPRODUCT"/>
            <EntitySet Name="ZJPPRODUCT_VI" EntityType="IBP.MASTER_DATA_API_SRV.ZJPPRODUCT_VI"/>
            <EntitySet Name="ZJPPRODUCTTrans" EntityType="IBP.MASTER_DATA_API_SRV.ZJPPRODUCTTrans"/>
            <EntitySet Name="ZJPPRODUCTMessage" EntityType="IBP.MASTER_DATA_API_SRV.ZJPPRODUCTMessage"/>
            <EntitySet Name="ZJPLOCATIONPRODUCT" EntityType="IBP.MASTER_DATA_API_SRV.ZJPLOCATIONPRODUCT"/>
            <EntitySet Name="ZJPLOCATION" EntityType="IBP.MASTER_DATA_API_SRV.ZJPLOCATION"/>
            <EntitySet Name="OTHERPRODUCT" EntityType="IBP.MASTER_DATA_API_SRV.OTHERPRODUCT"/>
           </EntityContainer>
          </Schema>
         </edmx:DataServices>
        </edmx:Edmx>"""
        get_response.return_value = Mock(ok=True, text=metadata)
        self.assertEqual(
            tools._discover_product_master_data_types("ZJPIBP1"),
            ["ZJPLOCATIONPRODUCT", "ZJPPRODUCT"],
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


_V2_METADATA = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
 <edmx:DataServices>
  <Schema Namespace="IBP.MASTER_DATA_API_SRV" xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
   <EntityType Name="ZJPPRODUCT">
     <Property Name="PRDID" Type="Edm.String" Nullable="false"/>
     <Property Name="UOMID" Type="Edm.String"/><Property Name="PRDDESCR" Type="Edm.String"/>
   </EntityType>
   <EntityType Name="ZJPLOCATIONPRODUCT">
     <Property Name="LOCID" Type="Edm.String" Nullable="false"/>
     <Property Name="PRDID" Type="Edm.String" Nullable="false"/>
   </EntityType>
   <EntityType Name="ZJPSOURCEPRODUCTION">
     <Property Name="LOCID" Type="Edm.String" Nullable="false"/>
     <Property Name="PRDID" Type="Edm.String" Nullable="false"/>
     <Property Name="PLEADTIME" Type="Edm.Decimal"/>
     <Property Name="PMINLOTSIZE" Type="Edm.Decimal"/><Property Name="PMAXLOTSIZE" Type="Edm.Decimal"/>
   </EntityType>
   <EntityType Name="ZJPLOCATION"><Property Name="LOCID" Type="Edm.String" Nullable="false"/></EntityType>
   <EntityContainer Name="C">
    <EntitySet Name="ZJPPRODUCT" EntityType="IBP.MASTER_DATA_API_SRV.ZJPPRODUCT"/>
    <EntitySet Name="ZJPLOCATIONPRODUCT" EntityType="IBP.MASTER_DATA_API_SRV.ZJPLOCATIONPRODUCT"/>
    <EntitySet Name="ZJPSOURCEPRODUCTION" EntityType="IBP.MASTER_DATA_API_SRV.ZJPSOURCEPRODUCTION"/>
    <EntitySet Name="ZJPLOCATION" EntityType="IBP.MASTER_DATA_API_SRV.ZJPLOCATION"/>
   </EntityContainer>
   <Annotations Target="IBP.MASTER_DATA_API_SRV.ZJPPRODUCT/PRDID" xmlns="http://docs.oasis-open.org/odata/ns/edm">
    <Annotation Term="Common.FieldControl" EnumMember="Common.FieldControlType/Mandatory"/>
   </Annotations>
  </Schema>
 </edmx:DataServices>
</edmx:Edmx>"""


def _route(data):
    """Fake requests.get: metadata, per-type rows, and LOCATION lookups."""
    def fake_get(url, params=None, **kwargs):
        if url.endswith("$metadata"):
            return Mock(ok=True, text=_V2_METADATA)
        name = url.rsplit("/", 1)[-1]
        rows = data.get(name, [])
        if name == "ZJPLOCATION":  # reference lookup: return only known locations
            rows = [{"LOCID": "1010"}]
        return Mock(ok=True, json=Mock(return_value={"d": {"results": rows}}))
    return fake_get


@patch.object(tools, "USE_MOCK_DATA", False)
@patch.object(tools, "IBP_BASE_URL", "https://ibp.example")
@patch.object(tools, "IBP_USER", "planner")
@patch.object(tools, "IBP_PASSWORD", "secret")
@patch.object(tools, "MASTER_DATA_TYPE_PREFIX", "ZJP")
class ProductHealthRulesTests(unittest.TestCase):
    def _run(self, data):
        with patch.object(tools.requests, "get", side_effect=_route(data)):
            return tools.run_product_master_data_health_check(
                product="TestFG01", planning_area="ZJPIBP1"
            )

    def test_reports_completeness_consistency_and_reference_errors(self):
        result = self._run({
            "ZJPPRODUCT": [{"PRDID": "TestFG01", "UOMID": "", "PRDDESCR": "FG"}],
            "ZJPLOCATIONPRODUCT": [{"LOCID": "1010", "PRDID": "TestFG01"}],
            "ZJPSOURCEPRODUCTION": [{
                "LOCID": "1020", "PRDID": "TestFG01", "PLEADTIME": "-1",
                "PMINLOTSIZE": "100", "PMAXLOTSIZE": "50",
            }],
        })
        codes = {(f["code"], f["severity"]) for r in result["master_data_results"] for f in r["results"]}
        product_codes = {(f["code"], f["severity"]) for f in result["product_level_findings"]}
        self.assertIn(("MISSING_REQUIRED_ATTRIBUTE", "error"), codes)   # UOMID blank
        self.assertIn(("NEGATIVE_VALUE", "error"), codes)                # PLEADTIME < 0
        self.assertIn(("INVALID_RANGE", "error"), codes)                 # min > max lot
        self.assertIn(("LOCATION_WITHOUT_LOCATION_PRODUCT", "warning"), product_codes)
        self.assertIn(("REFERENCE_NOT_FOUND", "error"), product_codes)   # 1020 not in LOCATION
        self.assertEqual(result["health"], "errors")
        self.assertGreaterEqual(result["error_count"], 4)

    def test_flags_missing_product_and_supply_source(self):
        result = self._run({})
        found = {(f["code"], f["severity"]) for f in result["product_level_findings"]}
        self.assertIn(("NO_MASTER_DATA_FOR_PRODUCT", "error"), found)   # no PRODUCT row
        self.assertIn(("COVERAGE_GAP", "warning"), found)               # no supply source
        self.assertIn("ZJPPRODUCT", result["checks_performed"]["coverage"]["types_without_rows"])

    def test_clean_product_reports_clean_and_lists_checks(self):
        result = self._run({
            "ZJPPRODUCT": [{"PRDID": "TestFG01", "UOMID": "EA", "PRDDESCR": "FG"}],
            "ZJPLOCATIONPRODUCT": [{"LOCID": "1010", "PRDID": "TestFG01"}],
            "ZJPSOURCEPRODUCTION": [{
                "LOCID": "1010", "PRDID": "TestFG01", "PLEADTIME": "2",
                "PMINLOTSIZE": "10", "PMAXLOTSIZE": "0",
            }],
        })
        self.assertEqual(result["health"], "clean")
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["warning_count"], 0)
        self.assertTrue(result["checks_performed"]["completeness"]["metadata_mandatory_fields"])
        self.assertIn("ZJPLOCATION", result["checks_performed"]["referential_lookups_values_checked"])


if __name__ == "__main__":
    unittest.main()
