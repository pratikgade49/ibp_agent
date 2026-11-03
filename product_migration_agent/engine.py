"""Deterministic conversion engine. No AI here: value maps -> derive rules -> checks -> SAP table split."""
import operator
import re

import pandas as pd

from . import config

NUMERIC_TYPES = {"QUAN", "CURR", "DEC"}
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq, "!=": operator.ne}


def _n(v):
    return str(v).strip().upper()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _match(row, cond):
    return all(_n(row.get(f, "")) in [_n(x) for x in (exp if isinstance(exp, list) else [exp])] for f, exp in cond.items())


def to_sap_frame(src: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    missing = [o for o in mapping["Oracle Field"] if o not in src.columns]
    if missing:
        raise ValueError(f"Mapping refers to columns that are not in the data file: {missing}")
    return pd.DataFrame({r["SAP Field"]: src[r["Oracle Field"]].values for _, r in mapping.iterrows()})


def convert(src: pd.DataFrame, mapping: pd.DataFrame, entries: dict, maps: dict):
    df = to_sap_frame(src, mapping)
    issues, changes, blank_ok = [], [], set()
    key = config.KEY_FIELD

    def issue(i, field, rule, sev, msg):
        issues.append({"source_row": i + 2, "MATNR": df.at[i, key], "field": field, "rule": rule,
                       "severity": sev, "message": msg, "value": df.at[i, field]})

    def change(i, field, new, by):
        changes.append({"source_row": i + 2, "MATNR": df.at[i, key], "field": field, "old": df.at[i, field], "new": new, "by": by})
        df.at[i, field] = new

    for field, m in maps.items():                                   # 1. value maps
        if field not in df.columns or not m:
            continue
        targets = set(m.values())
        for i, v in df[field].items():
            if v == "":
                continue
            if v in m:
                change(i, field, m[v], "value_map")
            elif v not in targets:
                issue(i, field, "value_map", "ERROR", f"no translation for source value {v!r}")

    flat = [(f, r) for f, e in entries.items() for r in e["rules"] if r["field"] in df.columns]
    for f, r in flat:                                               # 2. derive rules (ruleset order)
        if r["type"] != "conditional_set":
            continue
        for i in df.index:
            row = df.loc[i]
            for b in r["branches"]:
                if _match(row, b["if"]):
                    new = str(b["then"])
                    if r.get("mode", "set") == "flag":
                        if row[r["field"]] != new:
                            issue(i, r["field"], f"{f}:cond", "ERROR", f"expected {new!r}")
                    elif row[r["field"]] != new:
                        change(i, r["field"], new, f"{f}:conditional_set")
                    if new == "":
                        blank_ok.add((i, r["field"]))
                    break
            else:
                issue(i, r["field"], f"{f}:cond", "WARN", "no rule branch matched (value left as is)")

    for _, m in mapping.iterrows():                                 # 3. length checks from the mapping
        f, ln, typ = m["SAP Field"], m["SAP Length"], m["SAP Data Type"]
        if not ln.isdigit() or typ in NUMERIC_TYPES:
            continue
        for i, v in df[f].items():
            if len(v) > int(ln):
                if f in config.TRUNCATE_FIELDS:
                    change(i, f, v[:int(ln)], "truncate")
                else:
                    issue(i, f, "length", "ERROR", f"length {len(v)} > SAP length {ln}")

    for f, r in flat:                                               # 4. validation rules
        t, fld = r["type"], r["field"]
        if t == "conditional_set":
            continue
        for i, v in df[fld].items():
            if t == "not_blank" and v == "" and (i, fld) not in blank_ok:
                issue(i, fld, f"{f}:not_blank", "ERROR", "mandatory field is blank")
            elif t == "in_list" and v != "" and _n(v) not in [_n(x) for x in r["values"]]:
                issue(i, fld, f"{f}:in_list", "ERROR", f"not in allowed list {r['values']}")
            elif t == "max_length" and len(v) > int(r["length"]):
                issue(i, fld, f"{f}:max_length", "ERROR", f"longer than {r['length']}")
            elif t == "regex" and v != "" and not re.fullmatch(r["pattern"], v):
                issue(i, fld, f"{f}:regex", "ERROR", f"does not match {r['pattern']}")
            elif t == "compare_fields":
                a, b = _num(v), _num(df.at[i, r["other"]])
                if a is not None and b is not None and not OPS[r["op"]](a, b):
                    issue(i, fld, f"{f}:compare", "ERROR", f"{fld} {r['op']} {r['other']} violated ({v} vs {df.at[i, r['other']]})")

    tables = {}                                                     # 5. split into SAP tables
    for tbl, grp in mapping.groupby("SAP Table", sort=False):
        t = pd.DataFrame({key: df[key]})
        for k, val in config.STATIC_FIELDS.get(tbl, {}).items():
            t[k] = val
        for k, src_f in config.DERIVED_FIELDS.get(tbl, {}).items():
            t[k] = df[src_f]
        keys = config.TABLE_KEYS.get(tbl, [key])
        for k in keys:
            if k not in t.columns and k in df.columns:
                t[k] = df[k]
        for f in grp["SAP Field"]:
            t[f] = df[f]
        tables[tbl] = t[keys + [c for c in t.columns if c not in keys]].drop_duplicates(subset=keys)
    return tables, pd.DataFrame(issues), pd.DataFrame(changes), df
