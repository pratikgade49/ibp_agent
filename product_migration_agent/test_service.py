import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from product_migration_agent import config
from product_migration_agent.service import JobStore, convert_files

MAPPING = [
    ("Product Number", "MARA", "MATNR", "Material Number", "CHAR", "40", "Y"),
    ("Product Description", "MAKT", "MAKTX", "Material Description", "CHAR", "40", "Y"),
    ("Product Type", "MARA", "MTART", "Material Type", "CHAR", "4", "Y"),
    ("Base UOM", "MARA", "MEINS", "Base Unit", "UNIT", "3", "Y"),
    ("Gross Weight", "MARA", "BRGEW", "Gross Weight", "QUAN", "13", "N"),
    ("Net Weight", "MARA", "NTGEW", "Net Weight", "QUAN", "13", "N"),
    ("Plant", "MARC", "WERKS", "Plant", "CHAR", "4", "Y"),
    ("Procurement Type", "MARC", "BESKZ", "Procurement Type", "CHAR", "1", "Y"),
    ("MRP Type", "MARC", "DISMM", "MRP Type", "CHAR", "2", "Y"),
    ("Price Control", "MBEW", "VPRSV", "Price Control", "CHAR", "1", "Y"),
]
RULES = [
    ("MATNR", "Material Number", "Y", "", "Blank"), ("MAKTX", "Material Description", "Y", "", "Blank"),
    ("MTART", "Material Type", "Y", "FERT,ROH,HALB", "Blank"), ("MEINS", "Base Unit", "Y", "", "Blank"),
    ("BRGEW", "Gross Weight", "N", "", ""), ("NTGEW", "Net Weight", "N", "", "Net weight must not exceed gross weight"),
    ("WERKS", "Plant", "Y", "", "Blank"), ("BESKZ", "Procurement Type", "Y", "E,F,X", "Blank"),
    ("DISMM", "MRP Type", "Y", "IF PROC TYPE E then X0\nIF F THEN PD", "Blank"), ("VPRSV", "Price Control", "Y", "S,V", "Blank"),
]
DATA = [  # MATNR, desc, type, uom, gross, net, plant, proc, mrp, price ctl
    ("A1", "Pump", "RAW", "EACH", "10", "8", "1000", "E", "PD", "STD"),
    ("A2", "Valve " + "x" * 50, "FG", "EACH", "10", "12", "1100", "F", "PD", "STD"),   # long description, net > gross
    ("A3", "Gauge", "SFG", "EACH", "5", "4", "1000", "0", "PD", "STD"),               # proc type 0 is unknowable
]
FIXTURES = {
    "DISMM": {"rules": [{"type": "conditional_set", "field": "DISMM", "mode": "set", "branches": [
        {"if": {"BESKZ": "E"}, "then": "X0"}, {"if": {"BESKZ": "F"}, "then": "PD"}]}], "readback": "MRP type from procurement type.", "confidence": 0.9, "notes": []},
    "NTGEW": {"rules": [{"type": "compare_fields", "field": "NTGEW", "op": "<=", "other": "BRGEW"}], "readback": "Net <= gross.", "confidence": 0.9, "notes": []},
    "MTART": {"map": {"RAW": "ROH", "FG": "FERT", "SFG": "HALB"}, "confidence": 0.95, "notes": []},
    "MEINS": {"map": {"EACH": "EA"}, "confidence": 0.95, "notes": []},
    "VPRSV": {"map": {"STD": "S"}, "confidence": 0.95, "notes": []},
    "BESKZ": {"map": {"0": None}, "confidence": 0.3, "notes": ["cannot tell what 0 means"]},
}


class FakeLLM:
    def __init__(self, overrides=None, correction_response=None):
        self.calls, self.fx = 0, {**FIXTURES, **(overrides or {})}
        self.correction_response = correction_response

    def complete(self, system, user, max_tokens=2000):
        self.calls += 1
        if "CORRECTION_REQUEST:" in user:
            if self.correction_response is None:
                return json.dumps({"updates": [], "message": "Please specify the field and value."})
            return json.dumps(self.correction_response)
        return "```json\n" + json.dumps(self.fx[re.search(r"SAP_FIELD:\s*(\w+)", user).group(1)]) + "\n```"


def xlsx(rows, cols):
    b = io.BytesIO()
    pd.DataFrame(rows, columns=cols).to_excel(b, index=False)
    return b.getvalue()


def inputs():
    return (xlsx(DATA, ["Product Number", "Product Description", "Product Type", "Base UOM", "Gross Weight", "Net Weight",
                        "Plant", "Procurement Type", "MRP Type", "Price Control"]),
            xlsx(MAPPING, ["Oracle Field", "SAP Table", "SAP Field", "SAP Description", "SAP Data Type", "SAP Length", "Mandatory in S4"]),
            xlsx(RULES, ["SAP Field", "SAP Description", "Mandatory", "Data List", "Flag if"]))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(config, "CACHE_FILE", Path(self.tmp.name) / "cache.json")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def sheets(self, res):
        return pd.read_excel(io.BytesIO(res.workbook), sheet_name=None, dtype=str, keep_default_na=False)

    def test_single_sheet_output_keeps_input_columns(self):
        res = convert_files(*inputs(), llm=FakeLLM())
        s = self.sheets(res)
        self.assertEqual(set(s), {"CONVERTED_DATA"})
        self.assertEqual(list(s["CONVERTED_DATA"].columns), [
            "MATNR", "MAKTX", "MTART", "MEINS", "BRGEW", "NTGEW",
            "WERKS", "BESKZ", "DISMM", "VPRSV",
        ])
        self.assertEqual(list(s["CONVERTED_DATA"]["MTART"]), ["ROH", "FERT", "HALB"])
        self.assertEqual(list(s["CONVERTED_DATA"]["MEINS"]), ["EA", "EA", "EA"])
        self.assertEqual(list(s["CONVERTED_DATA"]["DISMM"])[:2], ["X0", "PD"])

    def test_mapping_for_missing_oracle_column_is_skipped(self):
        data, mapping, rules = inputs()
        source = pd.read_excel(io.BytesIO(data), dtype=str, keep_default_na=False)
        source = source.drop(columns=["Procurement Type"])
        result = convert_files(
            xlsx(source.values.tolist(), source.columns.tolist()), mapping, rules, llm=FakeLLM(),
        )

        converted = self.sheets(result)["CONVERTED_DATA"]
        self.assertNotIn("BESKZ", converted.columns)
        self.assertTrue(any("Procurement Type" in warning for warning in result.summary["warnings"]))
        self.assertEqual(len(converted), len(DATA))

    def test_unknowable_value_goes_to_human_not_guessed(self):
        res = convert_files(*inputs(), llm=FakeLLM())
        self.assertEqual([h["field"] for h in res.human_needed], ["BESKZ"])
        self.assertEqual(self.sheets(res)["CONVERTED_DATA"]["BESKZ"][2], "0")
        self.assertEqual(res.summary["ready"], 1)

    def test_preview_includes_issue_rows_and_descriptions(self):
        res = convert_files(*inputs(), llm=FakeLLM())
        preview = res.summary["preview"]
        self.assertEqual(len(preview), len(DATA))
        issue_row = next(row for row in preview if row["MATNR"] == "A2")
        self.assertTrue(any(issue["severity"] == "ERROR" and issue["field"] == "NTGEW"
                            and "violated" in issue["message"] for issue in issue_row["_row_issues"]))

    def test_low_confidence_rule_is_not_applied(self):
        llm = FakeLLM({"DISMM": {**FIXTURES["DISMM"], "confidence": 0.4}})
        res = convert_files(*inputs(), llm=llm)
        self.assertIn("DISMM", [h["field"] for h in res.human_needed if h["type"] == "rule"])
        self.assertEqual(list(self.sheets(res)["CONVERTED_DATA"]["DISMM"]), ["PD", "PD", "PD"])

    def test_llm_correction_updates_target_and_revalidates(self):
        llm = FakeLLM(correction_response={"updates": [
            {"field": "BESKZ", "value": "E", "material": "A3"}], "message": "Updated procurement type."})
        res = convert_files(*inputs(), llm=llm, correction_text="For material A3, change procurement type to E")
        workbook = self.sheets(res)["CONVERTED_DATA"]
        self.assertEqual(workbook.loc[2, "BESKZ"], "E")
        self.assertEqual(workbook.loc[2, "DISMM"], "X0")
        self.assertEqual(res.summary["ready"], 2)
        self.assertTrue(res.summary["correction_applied"])
        self.assertEqual(res.summary["changes_applied"], 13)

    def test_matnr_correction_wording_updates_only_named_material(self):
        data, mapping, rules = inputs()
        source = pd.read_excel(io.BytesIO(data), dtype=str, keep_default_na=False)
        source.loc[0, "Product Number"] = "WSW3MYM2HN"
        source["Product Group"] = ""
        mapping_frame = pd.read_excel(io.BytesIO(mapping), dtype=str, keep_default_na=False)
        mapping_frame.loc[len(mapping_frame)] = ["Product Group", "MARA", "MATKL", "Material Group", "CHAR", "9", "N"]
        rules_frame = pd.read_excel(io.BytesIO(rules), dtype=str, keep_default_na=False)
        rules_frame.loc[len(rules_frame)] = ["MATKL", "Material Group", "N", "", "Blank"]
        llm = FakeLLM(correction_response={"updates": [
            {"field": "MATKL", "value": "RHW430", "material": "WSW3MYM2HN"}], "message": "Updated material group."})
        res = convert_files(
            xlsx(source.values.tolist(), source.columns.tolist()),
            xlsx(mapping_frame.values.tolist(), mapping_frame.columns.tolist()),
            xlsx(rules_frame.values.tolist(), rules_frame.columns.tolist()),
            llm=llm,
            correction_text="Set MATKL as RHW430 for MATNR WSW3MYM2HN",
        )
        workbook = self.sheets(res)["CONVERTED_DATA"]
        self.assertEqual(workbook.loc[0, "MATKL"], "RHW430")
        self.assertEqual(workbook.loc[1:, "MATKL"].tolist(), ["", ""])
        self.assertTrue(res.summary["correction_applied"])

    def test_mbrsh_same_field_rule_maps_oil_to_o(self):
        data, mapping, rules = inputs()
        source = pd.read_excel(io.BytesIO(data), dtype=str, keep_default_na=False)
        source["Industry Sector"] = "OIL"
        mapping_frame = pd.read_excel(io.BytesIO(mapping), dtype=str, keep_default_na=False)
        mapping_frame.loc[len(mapping_frame)] = ["Industry Sector", "MARA", "MBRSH", "Industry Sector", "CHAR", "1", "Y"]
        rules_frame = pd.read_excel(io.BytesIO(rules), dtype=str, keep_default_na=False)
        rules_frame.loc[len(rules_frame)] = ["MBRSH", "Industry Sector", "Y", "Industry Sector If OIL then O", "Blank"]
        res = convert_files(
            xlsx(source.values.tolist(), source.columns.tolist()),
            xlsx(mapping_frame.values.tolist(), mapping_frame.columns.tolist()),
            xlsx(rules_frame.values.tolist(), rules_frame.columns.tolist()),
            llm=FakeLLM(),
        )
        workbook = self.sheets(res)["CONVERTED_DATA"]
        self.assertEqual(workbook["MBRSH"].tolist(), ["O", "O", "O"])
        self.assertNotIn("MBRSH", [item["field"] for item in res.human_needed])

    def test_unresolved_correction_is_not_claimed_as_applied(self):
        res = convert_files(*inputs(), llm=FakeLLM(), correction_text="Can this warning be ignored?")
        self.assertFalse(res.summary["correction_applied"])
        self.assertIn("specify", res.summary["correction_message"])

    def test_llm_cannot_target_material_not_named_by_user(self):
        llm = FakeLLM(correction_response={"updates": [
            {"field": "BESKZ", "value": "E", "material": "A3"}], "message": "Updated."})
        res = convert_files(*inputs(), llm=llm, correction_text="Set procurement type to E for the gauge")
        self.assertFalse(res.summary["correction_applied"])
        self.assertEqual(self.sheets(res)["CONVERTED_DATA"]["BESKZ"][2], "0")

    def test_second_run_uses_cache(self):
        llm = FakeLLM()
        convert_files(*inputs(), llm=llm)
        first = llm.calls
        res = convert_files(*inputs(), llm=llm)
        self.assertEqual(llm.calls, first)
        self.assertEqual(res.summary["rules_compiled_by_ai"], 0)

    def test_invalid_ai_rule_is_rejected(self):
        bad = {"rules": [{"type": "conditional_set", "field": "DISMM", "branches": [{"if": {"NOPE": "E"}, "then": "X0"}]}],
               "readback": "", "confidence": 0.99, "notes": []}
        res = convert_files(*inputs(), llm=FakeLLM({"DISMM": bad}))
        self.assertTrue(any(h["field"] == "DISMM" and "failed" in h["detail"] for h in res.human_needed))

    def test_bad_inputs(self):
        d, m, r = inputs()
        with self.assertRaises(ValueError):
            convert_files(d, b"not excel", r, llm=FakeLLM())
        with self.assertRaises(ValueError):
            convert_files(d, xlsx([("a",)], ["Oracle Field"]), r, llm=FakeLLM())
        with self.assertRaises(ValueError):
            convert_files(d, m, r, min_conf=2, llm=FakeLLM())

    def test_job_store_evicts_oldest(self):
        js = JobStore(limit=2)
        ids = [js.put(b"x", [{"MATNR": str(i)}], [], []) for i in range(3)]
        self.assertIsNone(js.get(ids[0]))
        self.assertIsNone(js.get_s4_records(ids[0]))
        self.assertEqual(js.get(ids[2]), b"x")
        self.assertEqual(js.get_s4_records(ids[2]), ([{"MATNR": "2"}], [], {}))


if __name__ == "__main__":
    unittest.main()
