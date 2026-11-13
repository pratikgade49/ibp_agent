import unittest
from unittest.mock import Mock, patch
from agent import tools

METADATA = b"""<Schema xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
<EntityType Name="ZJPSOURCEPRODUCTION">
<Property Name="PRATIO" Type="Edm.Decimal" Precision="18" Scale="6"/>
<Property Name="PLEADTIME" Type="Edm.Decimal"/>
<Property Name="PGOODSRECEIPTTIME" Type="Edm.Int32"/>
<Property Name="SOURCEID" Type="Edm.String"/>
</EntityType></Schema>"""

class MasterDataSerializationTests(unittest.TestCase):
    def test_decimal_fields_and_other_types(self):
        record = {"PRATIO": 1, "PLEADTIME": 0.25, "PGOODSRECEIPTTIME": 2, "SOURCEID": "PL01_Smartphone"}
        rows = tools._serialize_master_data_decimals(METADATA, "ZJPSOURCEPRODUCTION", [record])
        self.assertEqual(rows[0], {"PRATIO": "1", "PLEADTIME": "0.25", "PGOODSRECEIPTTIME": 2, "SOURCEID": "PL01_Smartphone"})
        self.assertEqual(record["PRATIO"], 1)

    def test_invalid_metadata_and_values(self):
        for metadata, entity, value in [(b"bad", "ZJPSOURCEPRODUCTION", 1), (METADATA, "UNKNOWN", 1), (METADATA, "ZJPSOURCEPRODUCTION", "NaN"), (METADATA, "ZJPSOURCEPRODUCTION", True)]:
            with self.subTest(entity=entity, value=value):
                with self.assertRaises(ValueError):
                    tools._serialize_master_data_decimals(metadata, entity, [{"PRATIO": value}])

    def test_confirmed_write_posts_decimal_string(self):
        session = Mock()
        session.get.return_value = Mock(ok=True, headers={"x-csrf-token": "token"}, content=METADATA)
        session.post.return_value = Mock(ok=True, content=b"", status_code=200)
        with patch.object(tools.requests, "Session", return_value=session):
            tools.import_master_data("ZJPSOURCEPRODUCTION", ["PRATIO"], [{"SOURCEID": "PL01_Smartphone", "LOCID": "PL01", "PRDID": "Smartphone", "PRATIO": 1}], confirm=True)
        self.assertEqual(session.post.call_args.kwargs["json"]["NavZJPSOURCEPRODUCTION"][0]["PRATIO"], "1")

    def test_unconfirmed_write_does_not_contact_sap(self):
        with patch.object(tools.requests, "Session") as session:
            result = tools.import_master_data("ZJPSOURCEPRODUCTION", ["PRATIO"], [{"PRATIO": 1}])
        self.assertEqual(result["status"], "confirmation_required")
        session.assert_not_called()

if __name__ == "__main__":
    unittest.main()