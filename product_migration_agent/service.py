"""Orchestration: 3 uploaded workbooks in -> converted S/4 workbook + summary out."""
import io
import json
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass

import pandas as pd
from openpyxl.styles import Font, PatternFill

from . import config
from .engine import convert, to_sap_frame
from .rules import compile_rules
from .util import clean, extract_json
from .value_maps import decide_value_maps

MAPPING_COLS = ["Oracle Field", "SAP Table", "SAP Field", "SAP Description", "SAP Data Type", "SAP Length", "Mandatory in S4"]
RULES_COLS = ["SAP Field", "SAP Description", "Mandatory", "Data List", "Flag if"]
_cache_lock = threading.Lock()


CORRECTION_SYSTEM = """You extract explicit data corrections from a user's message for an SAP S/4HANA migration.
Return only JSON: {"updates":[{"field":"SAP_FIELD","value":"literal value","material":null}],"message":"short explanation"}.
Resolve ordinary field descriptions to the exact SAP technical field from the supplied catalog.
Use material=null for a correction intended for every record. For wording such as "Set MATKL as RHW430 for MATNR WSW3MYM2HN", extract field MATKL, value RHW430, and material WSW3MYM2HN. Use an exact material identifier only when the user explicitly targets one or more records.
Never invent a replacement value or material identifier. If the request is a question, ambiguous, or does not specify a concrete value, return an empty updates list and explain briefly.
Values must be strings. To set a field blank, use an empty string."""


def _extract_manual_corrections(llm, correction_text: str | None, mapping: pd.DataFrame, df: pd.DataFrame):
    text = (correction_text or "").strip()
    if not text:
        return [], ""

    fields = mapping[["SAP Field", "SAP Description"]].drop_duplicates().to_dict(orient="records")
    user = (f"CORRECTION_REQUEST: {text}\n"
            f"SAP field catalog: {json.dumps(fields)}\n"
            f"Record key field: {config.KEY_FIELD}\n"
            'Return JSON in the required format. Do not return prose outside JSON.')
    try:
        result = extract_json(llm.complete(CORRECTION_SYSTEM, user, 2000))
        raw_updates = result.get("updates", [])
        if not isinstance(raw_updates, list):
            raise ValueError("updates must be a list")
        updates = []
        available_materials = set(df[config.KEY_FIELD].astype(str))
        for item in raw_updates:
            if not isinstance(item, dict):
                raise ValueError("an update was not a JSON object")
            field = str(item.get("field", "")).strip()
            value = item.get("value")
            material = item.get("material")
            if field not in df.columns or field == config.KEY_FIELD:
                raise ValueError(f"unknown or non-editable SAP field {field!r}")
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise ValueError(f"no literal value was provided for {field}")
            if material is not None:
                material = str(material).strip()
                if material not in available_materials:
                    raise ValueError(f"material {material!r} is not in the uploaded data")
                if material.casefold() not in text.casefold():
                    raise ValueError("a row-specific correction must name the exact material number")
            updates.append({"field": field, "value": str(value), "material": material})
        if not updates:
            return [], str(result.get("message") or "I couldn't identify a concrete field and value to change. Please specify both.")
        return updates, ""
    except Exception:  # noqa: BLE001 - malformed/ambiguous extraction must not be reported as applied
        return [], "I couldn't safely interpret that correction, so no changes were made. Please specify the field, replacement value, and material number if it should apply to only one record."


class _LazyLLM:
    """Creates the provider client (agent.llm, selected by LLM_PROVIDER) only if an AI call is actually needed."""
    def __init__(self):
        self._c = None

    def complete(self, system, user, max_tokens=2000):
        if self._c is None:
            from agent.llm import get_llm_client
            self._c = get_llm_client()
        return self._c.complete(system, user, max_tokens)


@dataclass
class ConversionResult:
    workbook: bytes
    summary: dict
    human_needed: list
    s4_records: list[dict]
    s4_error_materials: list[str]
    s4_field_map: dict[str, dict]


def _read(data: bytes, label: str, required=None) -> pd.DataFrame:
    if not data:
        raise ValueError(f"The {label} file is empty.")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise ValueError(f"The {label} file is larger than {config.MAX_UPLOAD_BYTES // 1024 // 1024} MB.")
    try:
        df = pd.read_excel(io.BytesIO(data), dtype=str, keep_default_na=False)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"The {label} file is not a readable .xlsx workbook ({e}).") from e
    df.columns = [str(c).strip() for c in df.columns]
    df = df.apply(lambda col: col.map(clean))
    missing = [c for c in (required or []) if c not in df.columns]
    if missing:
        raise ValueError(f"The {label} file is missing required column(s): {missing}.")
    if len(df) > config.MAX_ROWS:
        raise ValueError(f"The {label} file has more than {config.MAX_ROWS} rows.")
    return df


def _load_cache() -> dict:
    try:
        c = json.loads(config.CACHE_FILE.read_text())
    except (OSError, ValueError):
        c = {}
    return {"rules": c.get("rules", {}), "maps": c.get("maps", {})}


def _save_cache(c: dict) -> None:
    try:
        config.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.CACHE_FILE.write_text(json.dumps(c))
    except OSError:
        pass  # cache is an optimisation only


def _style(ws, row_severity=None):
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF", name="Arial")
        c.fill = PatternFill("solid", fgColor="1F4E78")
    for row_number, severity in (row_severity or {}).items():
        color = "FCE8E6" if severity == "ERROR" else "FFF2CC"
        for cell in ws[row_number]:
            cell.fill = PatternFill("solid", fgColor=color)
    ws.freeze_panes = "A2"
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(60, max(10, max(len(str(c.value or "")) for c in col[:200]) + 2))


def convert_files(data: bytes, mapping: bytes, rules: bytes, min_conf: float | None = None,
                  llm=None, manual_maps: dict | None = None, correction_text: str | None = None) -> ConversionResult:
    min_conf = config.DEFAULT_MIN_CONF if min_conf is None else min_conf
    if not 0 <= min_conf <= 1:
        raise ValueError("min_confidence must be between 0 and 1.")
    src, mp, rs = _read(data, "data"), _read(mapping, "mapping", MAPPING_COLS), _read(rules, "rules", RULES_COLS)
    missing_source_fields = sorted(set(mp["Oracle Field"]) - set(src.columns))
    mp = mp[mp["Oracle Field"].isin(src.columns)].copy()
    rs = rs[rs["SAP Field"].isin(set(mp["SAP Field"]))].copy()
    llm = llm or _LazyLLM()
    with _cache_lock:
        cache = _load_cache()
    entries, stats, warns = compile_rules(rs, mp, llm, cache["rules"], min_conf)
    if missing_source_fields:
        warns.append("Skipped mappings with Oracle columns absent from the data: " + ", ".join(missing_source_fields))
    sap_df = to_sap_frame(src, mp)
    maps, map_dec = decide_value_maps(sap_df, mp, entries, min_conf, llm, cache["maps"], manual_maps)
    with _cache_lock:
        _save_cache(cache)
    corrections, correction_message = _extract_manual_corrections(llm, correction_text, mp, sap_df)
    tables, issues, changes, converted_sap = convert(src, mp, entries, maps, corrections)

    bad = set(issues.loc[issues["severity"] == "ERROR", "MATNR"]) if len(issues) else set()
    ids = tables[next(iter(tables))]["MATNR"]
    status = pd.DataFrame({"MATNR": ids, "Load status": ["HAS ERRORS" if m in bad else "READY" for m in ids]})
    rule_dec = [{"SAP Field": f, "Status": e["status"], "Confidence": e["confidence"],
                 "Original rule text": " | ".join(f"{k}: {v}" for k, v in e["source_text"].items() if v),
                 "AI readback": e["readback"], "Notes": "; ".join(e["notes"])} for f, e in entries.items() if e["status"] != "approved"]
    human = [{"type": "value_translation", "field": d["SAP Field"], "detail": f"source value {d['Source value']!r}: {d['Notes'] or 'AI not confident'}"}
             for d in map_dec if d["Applied"] != "YES"]
    human += [{"type": "rule", "field": r["SAP Field"], "detail": f"{r['Status']} (confidence {r['Confidence']}): {r['Notes'] or r['AI readback']}"}
              for r in rule_dec if r["Status"] in ("needs_review", "failed")]
    summary = {"records": len(status), "ready": int((status["Load status"] == "READY").sum()), "with_errors": len(bad),
               "changes_applied": len(changes), "issues": len(issues), "rules_compiled_by_ai": stats["ai_compiled"],
               "rules_from_cache": stats["cached"], "human_decisions_needed": len(human), "warnings": warns}
    if correction_text:
        summary["correction_applied"] = bool(corrections)
        summary["correction_message"] = correction_message or f"Applied {len(corrections)} correction(s); validation and preview were regenerated."

    rename_map = {
        oracle_field: sap_field
        for oracle_field, sap_field in mp[["Oracle Field", "SAP Field"]].drop_duplicates().itertuples(index=False)
        if oracle_field in src.columns and sap_field
    }
    converted = src.rename(columns=rename_map).copy()
    for field in list(rename_map.values()):
        if field in converted_sap.columns:
            converted[field] = converted_sap[field].tolist()

    issues_by_row = {}
    row_severity = {}
    if len(issues):
        for item in issues.to_dict(orient="records"):
            source_row = int(item["source_row"])
            issues_by_row.setdefault(source_row, []).append({
                "field": str(item["field"]), "rule": str(item["rule"]),
                "severity": str(item["severity"]), "message": str(item["message"]),
            })
            if item["severity"] == "ERROR" or source_row not in row_severity:
                row_severity[source_row] = str(item["severity"])

    preview = []
    for index, row in converted.iterrows():
        source_row = int(index) + 2
        preview.append({
            **{str(k): "" if pd.isna(v) else str(v) for k, v in row.items()},
            "_row_issues": issues_by_row.get(source_row, []),
        })

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        converted.to_excel(w, sheet_name="CONVERTED_DATA", index=False)
        ws = w.book["CONVERTED_DATA"]
        _style(ws, row_severity)
    return ConversionResult(
        buf.getvalue(),
        {**summary, "preview": preview},
        human,
        converted_sap.to_dict(orient="records"),
        sorted(bad),
        [
            {
                "oracle_field": str(row["Oracle Field"]).strip(),
                "field": str(row["SAP Field"]),
                "sap_description": str(row["SAP Description"]).strip(),
            }
            for _, row in mp.iterrows()
        ],
    )


class JobStore:
    """In-memory results (same trial-friendly approach as the conversation stores in app.py)."""
    def __init__(self, limit: int = 20):
        self._jobs, self._records, self._limit, self._lock = OrderedDict(), {}, limit, threading.Lock()

    def put(self, workbook: bytes, s4_records=None, s4_error_materials=None, s4_field_map=None) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = workbook
            self._records[job_id] = (s4_records or [], s4_error_materials or [], s4_field_map or {})
            while len(self._jobs) > self._limit:
                expired_id, _ = self._jobs.popitem(last=False)
                self._records.pop(expired_id, None)
        return job_id

    def get(self, job_id: str):
        with self._lock:
            return self._jobs.get(job_id)

    def get_s4_records(self, job_id: str):
        with self._lock:
            return self._records.get(job_id)
