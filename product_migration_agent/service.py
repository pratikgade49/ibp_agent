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
from .util import clean
from .value_maps import decide_value_maps

MAPPING_COLS = ["Oracle Field", "SAP Table", "SAP Field", "SAP Description", "SAP Data Type", "SAP Length", "Mandatory in S4"]
RULES_COLS = ["SAP Field", "SAP Description", "Mandatory", "Data List", "Flag if"]
_cache_lock = threading.Lock()


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


def _style(ws):
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF", name="Arial")
        c.fill = PatternFill("solid", fgColor="1F4E78")
    ws.freeze_panes = "A2"
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(60, max(10, max(len(str(c.value or "")) for c in col[:200]) + 2))


def convert_files(data: bytes, mapping: bytes, rules: bytes, min_conf: float | None = None,
                  llm=None, manual_maps: dict | None = None) -> ConversionResult:
    min_conf = config.DEFAULT_MIN_CONF if min_conf is None else min_conf
    if not 0 <= min_conf <= 1:
        raise ValueError("min_confidence must be between 0 and 1.")
    src, mp, rs = _read(data, "data"), _read(mapping, "mapping", MAPPING_COLS), _read(rules, "rules", RULES_COLS)
    llm = llm or _LazyLLM()
    with _cache_lock:
        cache = _load_cache()
    entries, stats, warns = compile_rules(rs, mp, llm, cache["rules"], min_conf)
    sap_df = to_sap_frame(src, mp)
    maps, map_dec = decide_value_maps(sap_df, mp, entries, min_conf, llm, cache["maps"], manual_maps)
    with _cache_lock:
        _save_cache(cache)
    tables, issues, changes, converted_sap = convert(src, mp, entries, maps)

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

    rename_map = {
        oracle_field: sap_field
        for oracle_field, sap_field in mp[["Oracle Field", "SAP Field"]].drop_duplicates().itertuples(index=False)
        if oracle_field in src.columns and sap_field
    }
    converted = src.rename(columns=rename_map).copy()
    for field in list(rename_map.values()):
        if field in converted_sap.columns:
            converted[field] = converted_sap[field].tolist()

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        converted.to_excel(w, sheet_name="CONVERTED_DATA", index=False)
        ws = w.book["CONVERTED_DATA"]
        _style(ws)
    return ConversionResult(buf.getvalue(), summary, human)


class JobStore:
    """In-memory results (same trial-friendly approach as the conversation stores in app.py)."""
    def __init__(self, limit: int = 20):
        self._jobs, self._limit, self._lock = OrderedDict(), limit, threading.Lock()

    def put(self, workbook: bytes) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = workbook
            while len(self._jobs) > self._limit:
                self._jobs.popitem(last=False)
        return job_id

    def get(self, job_id: str):
        with self._lock:
            return self._jobs.get(job_id)
