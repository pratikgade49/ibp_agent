import unittest
from unittest.mock import patch

from product_migration_agent import s4_product
from product_migration_agent.test_s4_product import METADATA, FakeSession, FakeResponse, FakeMetadataLLM, field_map_for


class SeparateCreateTests(unittest.TestCase):
    def run_push(self, rows, field_map, llm):
        session = FakeSession()
        with patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2})), patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products(rows, field_map=field_map, llm=llm)
        return result, session

    def test_product_and_children_use_separate_requests_and_generated_key(self):
        row = {"MATNR": "P1", "MAKTX": "Pump", "MTART": "ROH", "WERKS": "PL01", "BESKZ": "E"}
        mappings = field_map_for(row)
        for item in mappings:
            if item["field"] == "WERKS":
                item = item.copy()
                item.update({"sap_description": "Plant", "api_attribute": "Plant", "level": "Plant"})
                mappings = [m for m in mappings if m["field"] != "WERKS"] + [item]
                break
        result, session = self.run_push([row], mappings, FakeMetadataLLM())
        self.assertEqual(result["created"], 1)
        payloads = {url.rsplit("/", 1)[-1]: options["json"] for url, options in session.post_calls}
        self.assertEqual(payloads["A_Product"]["Product"], "")
        self.assertEqual(payloads["A_Product"]["to_Description"], {"results": [{"Product": "", "Language": "EN", "ProductDescription": "Pump"}]})
        self.assertNotIn("A_ProductDescription", payloads)
        self.assertEqual(payloads["A_ProductPlant"]["Product"], "900001")
        self.assertEqual(payloads["A_ProductPlant"]["Plant"], "PL01")

    def test_missing_child_key_is_reported_and_child_not_posted(self):
        row = {"MATNR": "P1", "MAKTX": "Pump", "MTART": "ROH", "BESKZ": "E"}
        result, session = self.run_push([row], field_map_for(row), FakeMetadataLLM())
        self.assertEqual(result["partial"], 1)
        error = next(item for item in result["results"][0]["related_results"] if item["status"] == "failed")
        self.assertIn("Plant", error["error"])
        self.assertFalse(any(url.endswith("/A_ProductPlant") for url, _ in session.post_calls))

    def test_ignored_populated_field_is_reported(self):
        row = {"MATNR": "P1", "MAKTX": "Pump", "MTART": "ROH", "UNKNOWN": "value"}
        mapping = field_map_for(row) + [{"field": "UNKNOWN", "oracle_field": "Unknown"}]
        result, _ = self.run_push([row], mapping, FakeMetadataLLM(skipped_fields={"UNKNOWN"}))
        self.assertIn("UNKNOWN", result["results"][0]["ignored_fields"])

    def test_oracle_product_type_maps_without_ai_and_reaches_root(self):
        row = {"MATNR": "P1", "MAKTX": "Pump", "MTART": "ROH"}
        mapping = [
            {"field": "MAKTX", "oracle_field": "Description", "sap_description": "Material Description", "api_attribute": "Language, LongText"},
            {"field": "MTART", "oracle_field": "Type", "sap_description": "Material Type", "api_attribute": "Product Type", "level": "Base"},
        ]
        class NoAI:
            def complete(self, *args):
                raise AssertionError("Known source fields should not require AI")
        result, session = self.run_push([row], mapping, NoAI())
        self.assertEqual(result["created"], 1)
        self.assertEqual(session.post_calls[0][1]["json"]["ProductType"], "ROH")

    def test_source_key_maps_to_old_id_without_becoming_product_key(self):
        metadata = s4_product._metadata_model(METADATA.replace(
            b'<Property Name="ProductType" Type="Edm.String"/>',
            b'<Property Name="ProductType" Type="Edm.String"/><Property Name="ProductOldID" Type="Edm.String"/>',
        ))
        row = {"MATNR": "GADE_SARKAR"}
        mappings = [{"field": "MATNR", "oracle_field": "Product Number",
                     "sap_description": "Material Number", "api_attribute": "ProductOldID", "level": "Base"}]
        mapped = s4_product._map_source_fields(mappings, [row], metadata, None)
        validated = s4_product._validate_field_map(mapped, [row], metadata)
        payload = s4_product._row_payload(row, "A_Product", "", validated)
        self.assertEqual(payload, {"ProductOldID": "GADE_SARKAR"})

    def test_description_and_attribute_control_target_not_technical_field(self):
        metadata = s4_product._metadata_model(METADATA)
        row = {"ARBITRARY_CODE": "ROH"}
        mappings = [{"field": "ARBITRARY_CODE", "oracle_field": "Unrelated",
                     "sap_description": "Material Type", "api_attribute": "Product Type", "level": "Base"}]
        mapped = s4_product._map_source_fields(mappings, [row], metadata, None)
        self.assertEqual(mapped[0]["property"], "ProductType")

    def test_missing_product_type_stops_before_sap_post(self):
        row = {"MATNR": "P1", "MAKTX": "Pump"}
        result, session = self.run_push([row], field_map_for(row), FakeMetadataLLM())
        self.assertEqual(result["failed"], 1)
        self.assertIn("ProductType", result["results"][0]["error"])
        self.assertFalse(session.post_calls)

    def test_supply_planning_is_created_with_plant(self):
        metadata = METADATA.replace(
            b'<Property Name="ProcurementType" Type="Edm.String"/>',
            b'<Property Name="ProcurementType" Type="Edm.String"/><Property Name="MRPResponsible" Type="Edm.String"/><NavigationProperty Name="to_ProductSupplyPlanning" Relationship="API_PRODUCT_SRV.SupplyAssoc" ToRole="Supply"/>',
        ).replace(
            b'<Association Name="DescriptionAssoc">',
            b'<EntityType Name="A_ProductSupplyPlanningType"><Key><PropertyRef Name="Product"/><PropertyRef Name="Plant"/></Key><Property Name="Product" Type="Edm.String"/><Property Name="Plant" Type="Edm.String"/><Property Name="LotSizingProcedure" Type="Edm.String"/><Property Name="MRPType" Type="Edm.String"/><Property Name="MRPResponsible" Type="Edm.String"/><Property Name="PlannedDeliveryDurationInDays" Type="Edm.Decimal"/><Property Name="InHouseProductionTime" Type="Edm.Decimal"/></EntityType><Association Name="SupplyAssoc"><End Role="Plant" Type="API_PRODUCT_SRV.A_ProductPlantType"/><End Role="Supply" Type="API_PRODUCT_SRV.A_ProductSupplyPlanningType"/></Association><Association Name="DescriptionAssoc">',
        ).replace(
            b'<EntitySet Name="A_ProductPlant" EntityType="API_PRODUCT_SRV.A_ProductPlantType"/>',
            b'<EntitySet Name="A_ProductPlant" EntityType="API_PRODUCT_SRV.A_ProductPlantType"/><EntitySet Name="A_ProductSupplyPlanning" EntityType="API_PRODUCT_SRV.A_ProductSupplyPlanningType"/>',
        )
        row = {"MATNR": "P1", "MAKTX": "Pump", "MTART": "ROH", "WERKS": "PL01",
               "DISLS": "EX", "DISMM": "PD", "DISPO": "001", "PLIFZ": "36", "DZEIT": "13"}
        mappings = [
            {"field": "MAKTX", "oracle_field": "Description", "sap_description": "Material Description", "api_attribute": "Language, LongText"},
            {"field": "MTART", "oracle_field": "Type", "sap_description": "Material Type", "api_attribute": "Product Type", "level": "Base"},
            {"field": "WERKS", "oracle_field": "Plant Code", "sap_description": "Plant", "api_attribute": "Plant", "level": "Plant"},
            {"field": "DISLS", "oracle_field": "Lot Size", "sap_description": "Lot Size Key", "api_attribute": "LotSizingProcedure", "level": "Plant"},
            {"field": "DISMM", "oracle_field": "MRP Type", "sap_description": "MRP Type", "api_attribute": "MRPType", "level": "Plant"},
            {"field": "DISPO", "oracle_field": "MRP Controller", "sap_description": "MRP Controller", "api_attribute": "MRPResponsible", "level": "Plant"},
            {"field": "PLIFZ", "oracle_field": "Delivery", "sap_description": "Planned Delivery Time"},
            {"field": "DZEIT", "oracle_field": "Production", "sap_description": "In-house Production Time"},
        ]
        session = FakeSession()
        with patch.object(session, "get", side_effect=lambda url, **kw: FakeResponse(200, headers={"x-csrf-token": "csrf-test"}, content=metadata) if url.endswith("/$metadata") else FakeResponse(200)), patch("product_migration_agent.s4_product._s4_request_config", return_value=("https://s4.example", {"timeout": 2})), patch("product_migration_agent.s4_product.requests.Session", return_value=session):
            result = s4_product.push_products([row], field_map=mappings)
        self.assertEqual(result["created"], 1)
        plant = next(options["json"] for url, options in session.post_calls if url.endswith("/A_ProductPlant"))
        self.assertEqual((plant["MRPType"], plant["MRPResponsible"]), ("PD", "001"))
        self.assertEqual(plant["to_ProductSupplyPlanning"], {
            "Product": "900001", "Plant": "PL01", "LotSizingProcedure": "EX",
            "MRPType": "PD", "MRPResponsible": "001", "PlannedDeliveryDurationInDays": "36", "InHouseProductionTime": "13"})
        self.assertFalse(any(url.endswith("/A_ProductSupplyPlanning") for url, _ in session.post_calls))
        self.assertEqual(result["results"][0]["related_results"][0]["included_deep_entities"], ["to_ProductSupplyPlanning"])
