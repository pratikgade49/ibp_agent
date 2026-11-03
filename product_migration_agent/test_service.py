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
    def __init__(self, overrides=None):
        self.calls, self.fx = 0, {**FIXTURES, **(overrides or {})}

    def complete(self, system, user, max_tokens=2000):
        self.calls += 1
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

    def test_unknowable_value_goes_to_human_not_guessed(self):
        res = convert_files(*inputs(), llm=FakeLLM())
        self.assertEqual([h["field"] for h in res.human_needed], ["BESKZ"])
        self.assertEqual(self.sheets(res)["CONVERTED_DATA"]["BESKZ"][2], "0")
        self.assertEqual(res.summary["ready"], 1)

    def test_low_confidence_rule_is_not_applied(self):
        llm = FakeLLM({"DISMM": {**FIXTURES["DISMM"], "confidence": 0.4}})
        res = convert_files(*inputs(), llm=llm)
        self.assertIn("DISMM", [h["field"] for h in res.human_needed if h["type"] == "rule"])
        self.assertEqual(list(self.sheets(res)["CONVERTED_DATA"]["DISMM"]), ["PD", "PD", "PD"])

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
        ids = [js.put(b"x") for _ in range(3)]
        self.assertIsNone(js.get(ids[0]))
        self.assertEqual(js.get(ids[2]), b"x")


if __name__ == "__main__":
    unittest.main()
