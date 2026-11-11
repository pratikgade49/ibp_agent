"""Compile ruleset rows into structured rules.
Deterministic where possible (Mandatory=Y, plain value lists); the LLM is called only for free-text
rules, once per new/changed rule text (results are cached by the caller)."""
import hashlib
import json
import re

from .util import clean, extract_json

SIMPLE_LIST = re.compile(r"^[A-Za-z0-9_]+(\s*,\s*[A-Za-z0-9_]+)*$")
RULE_TYPES = {
    "not_blank": ["field"], "in_list": ["field", "values"], "conditional_set": ["field", "branches"],
    "max_length": ["field", "length"], "compare_fields": ["field", "op", "other"], "regex": ["field", "pattern"],
}
OPS = {"<", "<=", ">", ">=", "==", "!="}

SYSTEM = """You convert SAP data-migration rule text into JSON rules for a deterministic engine.
Return ONLY one JSON object: {"rules":[...],"readback":"<one plain-English sentence>","confidence":0-1,"notes":[...]}.
Allowed rule objects (use SAP field technical names only):
 {"type":"in_list","field":F,"values":[...]}
 {"type":"conditional_set","field":F,"mode":"set","branches":[{"if":{OTHER_FIELD:"val" or ["v1","v2"]},"then":"value"}]}   ("then":"" means leave blank)
 {"type":"max_length","field":F,"length":N}
 {"type":"compare_fields","field":F,"op":"<=","other":G}      op in < <= > >= == !=
 {"type":"regex","field":F,"pattern":"..."}
Rules: do NOT emit not_blank (handled elsewhere). Resolve phrases like "PROC TYPE" to the correct SAP field from the
provided field list. Never invent values. If something is ambiguous or unusable, leave it out, explain in "notes",
and lower "confidence". Branch order matters (first match wins).
Example text for field PRICE_CTL: "if material type is FERT then S, ROH then V" ->
{"rules":[{"type":"conditional_set","field":"PRICE_CTL","mode":"set","branches":[{"if":{"MTART":"FERT"},"then":"S"},{"if":{"MTART":"ROH"},"then":"V"}]}],
 "readback":"Price control is S for finished goods and V for raw materials.","confidence":0.9,"notes":[]}"""


def validate_rule(rule: dict, known_fields: set) -> list:
    t = rule.get("type")
    if t not in RULE_TYPES:
        return [f"unknown type {t!r}"]
    errs = [f"{t}: missing '{k}'" for k in RULE_TYPES[t] if k not in rule]
    if errs:
        return errs
    if rule["field"] not in known_fields:
        errs.append(f"unknown field {rule['field']}")
    if t == "in_list" and not (isinstance(rule["values"], list) and rule["values"]):
        errs.append("in_list: values must be a non-empty list")
    if t == "conditional_set":
        if not isinstance(rule["branches"], list) or not rule["branches"]:
            errs.append("conditional_set: branches must be a non-empty list")
        else:
            for b in rule["branches"]:
                if not isinstance(b, dict) or not isinstance(b.get("if"), dict) or "then" not in b:
                    errs.append("branch needs 'if' (object) and 'then'")
                    continue
                errs += [f"branch references unknown field {f}" for f in b["if"] if f not in known_fields]
    if t == "compare_fields":
        if rule["op"] not in OPS:
            errs.append(f"bad op {rule['op']}")
        if rule["other"] not in known_fields:
            errs.append(f"unknown field {rule['other']}")
    if t == "regex":
        if len(str(rule["pattern"])) > 200:
            errs.append("regex too long")
        else:
            try:
                re.compile(rule["pattern"])
            except re.error as e:
                errs.append(f"bad regex: {e}")
    if t == "max_length" and not str(rule["length"]).isdigit():
        errs.append("max_length: length must be an integer")
    return errs


def _ask_llm(llm, row, field_desc, known):
    user = (f"SAP_FIELD: {row['SAP Field']}\nDescription: {row['SAP Description']}\n"
            f"Mandatory: {row['Mandatory']}\nData List text: {row['Data List']!r}\nFlag-if text: {row['Flag if']!r}\n"
            f"Available SAP fields: {json.dumps(field_desc)}\n"
            f"If a simple 'If SOURCE then TARGET' statement does not name a different source field, treat it as a mapping on {row['SAP Field']} itself.")
    last = ""
    for _ in range(2):  # one retry with the validation error fed back
        try:
            out = extract_json(llm.complete(SYSTEM, user + (f"\nPrevious output was invalid: {last}" if last else ""), 2000))
            rules = out.get("rules", [])
            errs = [e for r in rules for e in validate_rule(r, known)]
            if not errs:
                return {"rules": rules, "readback": str(out.get("readback", "")),
                        "confidence": float(out.get("confidence", 0) or 0),
                        "notes": [str(n) for n in out.get("notes", [])]}, ""
            last = "; ".join(errs)
        except Exception as e:  # noqa: BLE001 - any model/parse failure is reported, never raised
            last = str(e)
    return None, last


def _self_value_branches(text, field, other_field_desc):
    if re.search(r"\bif\s+[A-Za-z0-9_.-]+(?:\s+[A-Za-z0-9_.-]+)+\s+then\b", text, re.IGNORECASE):
        return []
    if any(re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE)
           for name in other_field_desc if name and len(name) > 2):
        return []
    return [
        {"if": {field: source}, "then": target}
        for source, target in re.findall(
            r"\bif\s+([A-Za-z0-9_.-]+)\s+then\s+([A-Za-z0-9_.-]+)", text, re.IGNORECASE
        )
    ]


def compile_rules(ruleset, mapping, llm, cache: dict, min_conf: float):
    """Returns (entries_by_field, stats, warnings). `cache` is mutated with new AI results."""
    field_desc = dict(zip(ruleset["SAP Field"], ruleset["SAP Description"]))
    known = set(mapping["SAP Field"]) | set(ruleset["SAP Field"])
    sig = ",".join(sorted(known))
    out, stats = {}, {"ai_compiled": 0, "cached": 0, "deterministic": 0, "failed": 0}
    for _, row in ruleset.iterrows():
        f = row["SAP Field"]
        if not f:
            continue
        base = []
        if row["Mandatory"].upper() == "Y":
            base.append({"type": "not_blank", "field": f})
        dl, fl = row["Data List"], row["Flag if"]
        other_descriptions = [
            str(description) for other_field, description in field_desc.items() if other_field != f
        ]
        self_branches = _self_value_branches(dl, f, [*field_desc.keys(), *other_descriptions]) if dl else []
        if self_branches:
            base.append({"type": "conditional_set", "field": f, "mode": "set", "branches": self_branches})
        elif dl and SIMPLE_LIST.match(dl):
            base.append({"type": "in_list", "field": f, "values": [v.strip() for v in dl.split(",")]})
        entry = {"status": "approved", "confidence": 1.0, "readback": "", "notes": [],
                 "source_text": {"Mandatory": row["Mandatory"], "Data List": dl, "Flag if": fl}, "rules": list(base)}
        needs_ai = (dl and not SIMPLE_LIST.match(dl) and not self_branches) or (fl and fl.lower() != "blank")
        if needs_ai:
            key = hashlib.sha1("|".join(["rule-compiler-v2", f, row["Mandatory"], dl, fl, sig]).encode()).hexdigest()
            ai = cache.get(key)
            if ai:
                stats["cached"] += 1
            else:
                ai, err = _ask_llm(llm, row, field_desc, known)
                if ai:
                    cache[key] = ai
                    stats["ai_compiled"] += 1
            if not ai:
                entry.update(status="failed", confidence=0.0, notes=[f"AI could not compile this rule: {err}"])
                stats["failed"] += 1
            else:
                ok = ai["confidence"] >= min_conf
                entry.update(status="auto_approved" if ok else "needs_review", confidence=ai["confidence"],
                             readback=ai["readback"], notes=ai["notes"])
                if ok:  # base rules always apply; AI-derived rules only when confident
                    entry["rules"] += ai["rules"]
        else:
            stats["deterministic"] += 1
        out[f] = entry
    warns = []
    mand = dict(zip(mapping["SAP Field"], mapping["Mandatory in S4"]))
    for f, e in out.items():
        m = mand.get(f)
        if m and (m == "Y") != (e["source_text"]["Mandatory"].upper() == "Y"):
            warns.append(f"{f}: mapping says Mandatory={m}, ruleset says {e['source_text']['Mandatory'] or 'blank'}")
    warns += [f"{f}: in mapping but has no row in ruleset (only length checks apply)" for f in sorted(set(mapping["SAP Field"]) - set(out))]
    return out, stats, warns
