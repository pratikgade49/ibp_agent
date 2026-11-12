import json
import unittest
from unittest.mock import patch

from product_migration_agent import s4_product


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None, text="", content=b""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload or {}
        self.headers = headers or {}
        self.cookies = {"sap-session": "test"}
        self.text = text
        self.content = content

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.create_payload = {"d": {"Product": "900001"}}
        self.get_calls = []
        self.post_calls = []
        self.post_responses = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if url.endswith("/$metadata"):
            return FakeResponse(200, headers={"x-csrf-token": "csrf-test"}, content=METADATA)
        if url.endswith("/A_Product"):
            return FakeResponse(200, {"d": {"results": []}})
        if any(f"A_Product('{material}')" in url for material in self.existing):
            return FakeResponse(200)
        return FakeResponse(404)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if self.post_responses:
            return self.post_responses.pop(0)
        return FakeResponse(201, self.create_payload)


METADATA = b'''<?xml version="1.0"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
    <edmx:DataServices>
        <Schema xmlns="http://schemas.microsoft.com/ado/2008/09/edm" Namespace="API_PRODUCT_SRV">
            <EntityType Name="A_ProductType"><Key><PropertyRef Name="Product"/></Key>
                <Property Name="Product" Type="Edm.String"/><Property Name="ProductType" Type="Edm.String"/>
                <Property Name="IndustrySector" Type="Edm.String"/><Property Name="BaseUnit" Type="Edm.String"/>
                <Property Name="ProductGroup" Type="Edm.String"/><Property Name="Division" Type="Edm.String"/>
                <Property Name="GrossWeight" Type="Edm.Decimal"/><Property Name="NetWeight" Type="Edm.Decimal"/>
                <Property Name="WeightUnit" Type="Edm.String"/><Property Name="IsMarkedForDeletion" Type="Edm.Boolean"/>
                <NavigationProperty Name="to_Description" Relationship="API_PRODUCT_SRV.DescriptionAssoc" ToRole="Description"/>
                <NavigationProperty Name="to_Plant" Relationship="API_PRODUCT_SRV.PlantAssoc" ToRole="Plant"/>
                <NavigationProperty Name="to_Valuation" Relationship="API_PRODUCT_SRV.ValuationAssoc" ToRole="Valuation"/>
            </EntityType>
            <EntityType Name="A_ProductDescriptionType"><Key><PropertyRef Name="Product"/><PropertyRef Name="Language"/></Key>
                <Property Name="Product" Type="Edm.String"/><Property Name="Language" Type="Edm.String"/>
                <Property Name="ProductDescription" Type="Edm.String"/>
            </EntityType>
            <EntityType Name="A_ProductPlantType"><Key><PropertyRef Name="Product"/><PropertyRef Name="Plant"/></Key>
                <Property Name="Product" Type="Edm.String"/><Property Name="Plant" Type="Edm.String"/><Property Name="MRPType" Type="Edm.String"/>
                <Property Name="ProcurementType" Type="Edm.String"/>
            </EntityType>
            <EntityType Name="A_ProductValuationType"><Key><PropertyRef Name="Product"/><PropertyRef Name="ValuationArea"/></Key>
                <Property Name="Product" Type="Edm.String"/><Property Name="ValuationArea" Type="Edm.String"/>
                <Property Name="ValuationClass" Type="Edm.String"/><Property Name="PriceControl" Type="Edm.String"/>
                <Property Name="StandardPrice" Type="Edm.Decimal"/>
            </EntityType>
            <Association Name="DescriptionAssoc"><End Role="Product" Type="API_PRODUCT_SRV.A_ProductType"/>
                <End Role="Description" Type="API_PRODUCT_SRV.A_ProductDescriptionType"/></Association>
            <Association Name="PlantAssoc"><End Role="Product" Type="API_PRODUCT_SRV.A_ProductType"/>
                <End Role="Plant" Type="API_PRODUCT_SRV.A_ProductPlantType"/></Association>
            <Association Name="ValuationAssoc"><End Role="Product" Type="API_PRODUCT_SRV.A_ProductType"/>
                <End Role="Valuation" Type="API_PRODUCT_SRV.A_ProductValuationType"/></Association>
            <EntityContainer Name="API_PRODUCT_SRV_Entities">
                <EntitySet Name="A_Product" EntityType="API_PRODUCT_SRV.A_ProductType"/>
                <EntitySet Name="A_ProductDescription" EntityType="API_PRODUCT_SRV.A_ProductDescriptionType"/>
                <EntitySet Name="A_ProductPlant" EntityType="API_PRODUCT_SRV.A_ProductPlantType"/>
                <EntitySet Name="A_ProductValuation" EntityType="API_PRODUCT_SRV.A_ProductValuationType"/>
            </EntityContainer>
        </Schema>
    </edmx:DataServices>
</edmx:Edmx>'''

FIELD_MAP = [
        {"field": "MATNR", "entity": "A_Product", "property": "Product", "navigation": ""},
        {"field": "MTART", "entity": "A_Product", "property": "ProductType", "navigation": ""},
        {"field": "MBRSH", "entity": "A_Product", "property": "IndustrySector", "navigation": ""},
        {"field": "MEINS", "entity": "A_Product", "property": "BaseUnit", "navigation": ""},
        {"field": "BRGEW", "entity": "A_Product", "property": "GrossWeight", "navigation": ""},
        {"field": "MATKL", "entity": "A_Product", "property": "ProductGroup", "navigation": ""},
        {"field": "MAKTX", "entity": "A_ProductDescription", "property": "ProductDescription", "navigation": "to_Description"},
        {"field": "WERKS", "entity": "A_ProductPlant", "property": "Plant", "navigation": "to_Plant"},
        {"field": "WERKS", "entity": "A_ProductValuation", "property": "ValuationArea", "navigation": "to_Valuation"},
        {"field": "DISMM", "entity": "A_ProductPlant", "property": "MRPType", "navigation": "to_Plant"},
        {"field": "BESKZ", "entity": "A_ProductPlant", "property": "ProcurementType", "navigation": "to_Plant"},
        {"field": "BKLAS", "entity": "A_ProductValuation", "property": "ValuationClass", "navigation": "to_Valuation"},
        {"field": "VPRSV", "entity": "A_ProductValuation", "property": "PriceControl", "navigation": "to_Valuation"},
        {"field": "STPRS", "entity": "A_ProductValuation", "property": "StandardPrice", "navigation": "to_Valuation"},
]
for _item in FIELD_MAP:
    _item["oracle_field"] = _item["field"]
    _item["sap_description"] = _item["field"]
ROOT_FIELD_MAP = [item for item in FIELD_MAP if item["entity"] == "A_Product"]


def field_map_for(*rows):
    fields = set().union(*(row.keys() for row in rows))
    return [mapping for mapping in FIELD_MAP if mapping["field"] in fields]


class FakeMetadataLLM:
    def __init__(self, property_overrides=None, skipped_fields=None, target_overrides=None):
        self.property_overrides = property_overrides or {}
        self.skipped_fields = set(skipped_fields or [])
        self.target_overrides = target_overrides or {}
        self.source_fields = []

    def complete(self, _system, user, _max_tokens=4000):
        marker = "Source fields: "
        start = user.index(marker) + len(marker)
        end = user.index("\nS/4 metadata options:", start)
        sources = json.loads(user[start:end])
        self.source_fields = sources
        targets = {item["field"]: item for item in FIELD_MAP}
        targets.update(self.target_overrides)
        mappings = []
        for source in sources:
            if source["field"] in self.skipped_fields:
                continue
            target = targets[source["field"]]
            mappings.append({
                **source,
                "entity": target["entity"],
                "property": self.property_overrides.get(source["field"], target["property"]),
                "navigation": target["navigation"],
            })
        return json.dumps({"mappings": mappings})


class S4ProductTests(unittest.TestCase):
    def setUp(self):
        llm_patch = patch("agent.llm.get_llm_client", return_value=FakeMetadataLLM())
        llm_patch.start()
        self.addCleanup(llm_patch.stop)

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_plant_lot_size_is_sent_in_nested_supply_planning(self, _request_config):
        session = FakeSession()
        metadata = METADATA.replace(
            b'<Property Name="ProcurementType" Type="Edm.String"/>',
            b'<Property Name="ProcurementType" Type="Edm.String"/><NavigationProperty Name="to_ProductSupplyPlanning" Relationship="API_PRODUCT_SRV.SupplyAssoc" ToRole="Supply"/>',
        )
        row = {"MATNR": "P-1", "MAKTX": "Pump", "WERKS": "PL01", "DISLS": "EX", "DISMM": "PD", "BESKZ": "F"}
        llm = FakeMetadataLLM(skipped_fields={"DISLS"})  # Model chooses valuation for WERKS; plant mapping must still be correct.
        with patch.object(session, "get", side_effect=lambda url, **kw: FakeResponse(200, headers={"x-csrf-token": "csrf-test"}, content=metadata) if url.endswith("/$metadata") else FakeResponse(200)):
            with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
                result = s4_product.push_products([row], field_map=field_map_for(row), llm=llm)
        self.assertEqual(result["created"], 1)
        plant = session.post_calls[1][1]["json"]
        self.assertEqual(plant["to_ProductSupplyPlanning"], {"Product": "900001", "Plant": "PL01", "LotSizingProcedure": "EX", "MRPType": "PD", "ProcurementType": "F"})
    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_creates_product_with_required_description_and_csrf(self, _request_config):
        session = FakeSession()
        row = {
            "MATNR": "P-1", "MTART": "FERT", "MBRSH": "C", "MEINS": "EA",
            "BRGEW": "209.83", "MATKL": "RHW430", "MAKTX": "Pump", "WERKS": "1000", "DISMM": "PD",
            "BKLAS": "3000", "VPRSV": "S", "STPRS": "12.50",
        }
        session.create_payload = {"d": {"Product": "900001"}}
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products([row], field_map=field_map_for(row))

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["failed"], 0)
        token_options = session.get_calls[0][1]
        self.assertEqual(token_options["headers"]["x-csrf-token"], "Fetch")
        self.assertEqual(len(session.post_calls), 3)
        product_url, product_options = session.post_calls[0]
        self.assertTrue(product_url.endswith("/A_Product"))
        self.assertEqual(product_options["headers"]["x-csrf-token"], "csrf-test")
        self.assertEqual(product_options["headers"]["csrf-token"], "csrf-test")
        self.assertEqual(product_options["json"]["Product"], "")
        self.assertEqual(product_options["json"]["ProductGroup"], "RHW430")
        self.assertEqual(product_options["json"]["GrossWeight"], "209.83")
        self.assertEqual(product_options["json"]["to_Description"], {"results": [{
            "Product": "", "Language": "EN", "ProductDescription": "Pump",
        }]})
        self.assertTrue(all("A_Product('900001')/" in url for url, _ in session.post_calls[1:]))
        self.assertEqual(result["results"][0]["source_material"], "P-1")
        self.assertEqual(result["results"][0]["product_id"], "900001")
        self.assertEqual(result["results"][0]["material"], "900001")

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_missing_product_description_is_rejected_before_create(self, _request_config):
        session = FakeSession()
        row = {"MATNR": "P-MIN", "MTART": "", "MATKL": ""}
        key_map = [{"field": "MATNR", "entity": "A_Product", "property": "Product", "navigation": ""}]
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products([row], field_map=key_map)

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["results"][0]["status"], "invalid_mapping_or_values")
        self.assertIn("requires a product description", result["results"][0]["error"])
        self.assertEqual(session.post_calls, [])

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_description_is_deep_inserted_for_internal_number_assignment(self, _request_config):
        session = FakeSession()
        row = {"MATNR": "P-DESC", "MTART": "ROH", "MAKTX": "Anchor Bolt 02"}
        mappings = [item for item in FIELD_MAP if item["field"] in {"MTART", "MAKTX"}]
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products([row], field_map=mappings)

        self.assertEqual(result["created"], 1)
        payload = session.post_calls[0][1]["json"]
        self.assertEqual(payload["ProductType"], "ROH")
        self.assertEqual(payload["Product"], "")
        self.assertEqual(payload["to_Description"], {"results": [{
            "Product": "", "ProductDescription": "Anchor Bolt 02", "Language": "EN",
        }]})
        self.assertEqual(len(session.post_calls), 1)

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_description_field_is_mapped_when_ai_omits_it(self, _request_config):
        session = FakeSession()
        row = {"MATNR": "P-DESC-FALLBACK", "MTART": "ROH", "ZZTXT": "Anchor Bolt 02"}
        field_map = [
            {"field": "MTART", "oracle_field": "Material Type", "sap_description": "Material Type"},
            {"field": "ZZTXT", "oracle_field": "Product Description", "sap_description": "Product Description"},
        ]
        llm = FakeMetadataLLM(skipped_fields={"ZZTXT"})
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products([row], field_map=field_map, llm=llm)

        self.assertEqual(result["created"], 1)
        self.assertEqual(
            session.post_calls[0][1]["json"]["to_Description"],
            {"results": [{
                "Product": "", "Language": "EN", "ProductDescription": "Anchor Bolt 02",
            }]},
        )

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_related_create_failure_is_reported_as_partial_product_creation(self, _request_config):
        session = FakeSession()
        session.post_responses = [
            FakeResponse(201, {"d": {"Product": "900001"}}),
            FakeResponse(400, text="plant rejected"),
        ]
        row = {"MATNR": "P-PARTIAL", "MTART": "FERT", "MAKTX": "Product text", "WERKS": "1000"}
        plant_target = next(item for item in FIELD_MAP if item["field"] == "WERKS" and item["property"] == "Plant")
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products(
                [row], field_map=field_map_for(row),
                llm=FakeMetadataLLM(target_overrides={"WERKS": plant_target}),
            )

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["partial"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["results"][0]["status"], "created_with_errors")
        self.assertEqual(result["results"][0]["related_results"][0]["error"], "plant rejected")

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_source_key_is_not_used_for_s4_duplicate_preflight(self, _request_config):
        session = FakeSession(existing={"EXISTS"})
        rows = [
            {"MATNR": "EXISTS", "MTART": "FERT", "MBRSH": "C", "MEINS": "EA", "MAKTX": "Existing product"},
            {"MATNR": "BAD", "MTART": "FERT", "MBRSH": "C", "MEINS": "EA", "MAKTX": "Skipped product"},
        ]
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products(rows, {"BAD"}, field_map=field_map_for(*rows))

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["already_exists"], 0)
        self.assertEqual(result["skipped_with_errors"], 1)
        self.assertEqual(len(session.post_calls), 1)

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_product_create_failure_is_reported(self, _request_config):
        session = FakeSession()
        session.post = lambda url, **kwargs: FakeResponse(400, text="invalid product")
        row = {"MATNR": "P-2", "MTART": "FERT", "MBRSH": "C", "MEINS": "EA", "MAKTX": "Product"}
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products([row], field_map=field_map_for(row))

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["partial"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["results"][0]["status"], "product_create_failed")

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_related_entity_mapping_is_omitted_from_product_create(self, _request_config):
        session = FakeSession()
        row = {"MATNR": "P-PLANT", "MTART": "FERT", "MBRSH": "C", "MEINS": "EA", "MAKTX": "Product", "WERKS": "1000"}
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products([row], field_map=field_map_for(row))
        self.assertEqual(result["created"], 1)
        self.assertEqual(len(session.post_calls), 2)
        self.assertNotIn("Plant", session.post_calls[0][1]["json"])
        self.assertIn("/A_Product('900001')/", session.post_calls[1][0])

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_procurement_type_maps_to_plant_entity_metadata(self, _request_config):
        session = FakeSession()
        row = {"MATNR": "P-PROC", "BESKZ": "E", "WERKS": "1000", "MAKTX": "Product"}
        source_map = [
            {"field": "BESKZ", "oracle_field": "Procurement Type", "sap_description": "Procurement Type"},
            {"field": "WERKS", "oracle_field": "Plant Code", "sap_description": "Plant"},
            {"field": "MAKTX", "oracle_field": "Product Description", "sap_description": "Product Description"},
        ]
        plant_target = next(item for item in FIELD_MAP if item["field"] == "WERKS" and item["property"] == "Plant")
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products(
                [row], field_map=source_map,
                llm=FakeMetadataLLM(target_overrides={"WERKS": plant_target}),
            )

        self.assertEqual(result["created"], 1)
        self.assertEqual(session.post_calls[1][1]["json"], {
            "Product": "900001", "Plant": "1000", "ProcurementType": "E",
        })

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_ai_target_not_in_metadata_is_skipped(self, _request_config):
        session = FakeSession()
        field_map = [dict(item) for item in FIELD_MAP]
        row = {"MATNR": "P-3", "MTART": "FERT", "MBRSH": "C", "MEINS": "EA", "MAKTX": "Product", "MATKL": "RHW430"}
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products(
                [row], field_map=field_map,
                llm=FakeMetadataLLM({"MATKL": "ProductGroupTypo"}),
            )
        self.assertEqual(result["created"], 1)
        self.assertNotIn("ProductGroup", session.post_calls[0][1]["json"])

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_s4_generated_product_number_is_not_mapped(self, _request_config):
        session = FakeSession()
        row = {"MATNR": "P-NOKEY", "MTART": "FERT", "MAKTX": "Product"}
        field_map = [item for item in FIELD_MAP if item["field"] in {"MATNR", "MTART", "MAKTX"}]
        llm = FakeMetadataLLM()
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products([row], field_map=field_map, llm=llm)
        self.assertEqual(result["created"], 1)
        self.assertEqual([source["field"] for source in llm.source_fields], ["MTART", "MAKTX"])
        self.assertNotIn("MATNR", [source["field"] for source in llm.source_fields])
        self.assertEqual(session.post_calls[0][1]["json"]["Product"], "")

    def test_fields_without_source_descriptors_are_skipped(self):
        session = FakeSession()
        with patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2})), \
             patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products([{"MATNR": "P-4", "MTART": "FERT", "MATKL": "RHW430"}])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(session.post_calls, [])

    def test_mapping_check_reports_matched_and_missing_fields(self):
        row = {"MATNR": "P-5", "MTART": "FERT", "MATKL": "RHW430"}
        field_map = [dict(item) for item in FIELD_MAP if item["field"] in {"MATNR", "MTART"}]
        next(item for item in field_map if item["field"] == "MTART")["oracle_field"] = "Oracle Product Type"
        response = FakeResponse(200, content=METADATA)
        with patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2})), \
             patch("product_migration_agent.s4_product.requests.get", return_value=response) as get:
            report = s4_product.check_product_mapping([row], field_map)

        self.assertEqual(get.call_count, 2)
        self.assertEqual(
            get.call_args_list[0].args[0],
            "https://s4.example/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product",
        )
        self.assertTrue(get.call_args_list[1].args[0].endswith("/API_PRODUCT_SRV/$metadata"))
        self.assertTrue(report["ready"])
        self.assertEqual(report["matched"], 1)
        source_key = next(item for item in report["mappings"] if item["field"] == "MATNR")
        self.assertEqual(source_key["status"], "optional_unmapped")
        self.assertIn("generated by SAP", source_key["message"])
        missing = next(item for item in report["mappings"] if item["field"] == "MATKL")
        self.assertEqual(missing["status"], "optional_unmapped")
        self.assertEqual(missing["message"], "Optional value will be omitted; select a target to send it")
        self.assertEqual(report["unmatched"], 0)
        self.assertEqual(report["optional_unmapped"], 2)
        matched = next(item for item in report["mappings"] if item["field"] == "MTART")
        self.assertEqual(matched["property"], "ProductType")
        self.assertEqual(matched["oracle_field"], "Oracle Product Type")
        self.assertTrue(report["target_options"])
        self.assertEqual(
            {option["entity"] for option in report["target_options"]},
            {"A_Product", "A_ProductDescription", "A_ProductPlant", "A_ProductValuation"},
        )

    def test_metadata_marks_only_product_key_required_for_product(self):
        metadata = s4_product._metadata_model(METADATA)
        product_type = metadata["entity_types"][metadata["root_type"]]
        self.assertEqual(product_type["required_properties"], set())
        self.assertEqual(product_type["generated_properties"], {"Product"})
        self.assertIn("ProductType", product_type["properties"])

    def test_decimal_values_are_validated_and_serialized_as_strings(self):
        self.assertEqual(s4_product._json_value("209.83", "Edm.Decimal", "GrossWeight"), "209.83")
        with self.assertRaisesRegex(ValueError, "not a valid number"):
            s4_product._json_value("not-a-number", "Edm.Decimal", "GrossWeight")
        with self.assertRaisesRegex(ValueError, "not a valid number"):
            s4_product._json_value("NaN", "Edm.Decimal", "GrossWeight")

    def test_generated_id_can_be_read_from_location_header(self):
        response = FakeResponse(
            201,
            headers={"Location": "https://s4.example/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product('900002')"},
        )
        self.assertEqual(s4_product._generated_product_id(response), "900002")

    def test_metadata_detects_non_creatable_product_entity_set(self):
        read_only_metadata = METADATA.replace(
            b'<EntitySet Name="A_Product"',
            b'<EntitySet xmlns:sap="urn:sap" sap:creatable="false" Name="A_Product"',
        )
        metadata = s4_product._metadata_model(read_only_metadata)
        self.assertFalse(metadata["root_creatable"])

    @patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2}))
    def test_gateway_create_not_allowed_is_reported_as_capability_failure(self, _request_config):
        session = FakeSession()
        session.post = lambda url, **kwargs: FakeResponse(
            400,
            {
                "error": {
                    "code": "GW/NOT_ALLOWED",
                    "message": {"value": "Create operation not allowed on entity."},
                    "innererror": {
                        "transactionid": "gateway-tx-1",
                        "errordetails": [{"code": "GW/DETAIL", "message": "Insert rejected"}],
                    },
                }
            },
            headers={"x-vcap-request-id": "gateway-request-1"},
        )
        row = {"MATNR": "P-READONLY", "MTART": "FERT", "MAKTX": "Product"}
        with patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products([row], field_map=field_map_for(row))

        failure = result["results"][0]
        self.assertEqual(failure["status"], "create_not_allowed")
        self.assertEqual(failure["http_status"], 400)
        self.assertEqual(failure["method"], "POST")
        self.assertTrue(failure["endpoint"].endswith("/A_Product"))
        self.assertIn("does not distinguish", failure["error"])
        self.assertIn("GW/NOT_ALLOWED", failure["error"])
        self.assertIn("GW/DETAIL: Insert rejected", failure["error"])
        self.assertIn("gateway-tx-1", failure["error"])
        self.assertEqual(failure["sap_request_id"], "gateway-request-1")
        self.assertEqual(failure["request_fields"], [
            "IndustrySector", "IsMarkedForDeletion", "Product", "ProductType",
        ])

    def test_mapping_check_without_api_columns_lists_populated_fields(self):
        with patch("product_migration_agent.s4_product._s4_request_config") as request_config:
            report = s4_product.check_product_mapping([{"MATNR": "P-6", "MTART": "FERT"}], [])
        request_config.assert_not_called()
        self.assertEqual(report["ready"], False)
        self.assertEqual({item["field"] for item in report["mappings"]}, {"MTART"})


if __name__ == "__main__":
    unittest.main()