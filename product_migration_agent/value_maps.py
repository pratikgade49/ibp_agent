"""AI decides source->SAP value translations for values SAP would reject. Low confidence = not applied."""
from . import config
from .util import extract_json

CHAR_LIKE = {"CHAR", "UNIT", "NUMC"}
SYSTEM = "You are an SAP S/4HANA data migration expert. Output ONE JSON object only."


def decide_value_maps(df, mapping, entries, min_conf, llm, cache: dict, manual: dict | None = None):
    derived = {r["field"] for e in entries.values() for r in e["rules"] if r["type"] == "conditional_set"}
    lists = {r["field"]: [str(v).upper() for v in r["values"]]
             for e in entries.values() for r in e["rules"] if r["type"] == "in_list"}
    maps = {f: dict(m) for f, m in (manual or {}).items() if isinstance(m, dict)}
    decisions = []
    for _, m in mapping.iterrows():
        f, typ, ln = m["SAP Field"], m["SAP Data Type"], m["SAP Length"]
        if f in derived or f in config.TRUNCATE_FIELDS or f == config.KEY_FIELD:
            continue
        allowed = lists.get(f)
        sized = typ in CHAR_LIKE and ln.isdigit()

        def invalid(v):
            if v in maps.get(f, {}):
                return False
            return (allowed is not None and v.upper() not in allowed) or (sized and len(v) > int(ln))

        bad = sorted(v for v in df[f].unique() if v and invalid(v))
        if not bad:
            continue
        todo = [v for v in bad if v not in cache.get(f, {})]
        if todo:
            user = (f"SAP_FIELD: {f}\nDescription: {m['SAP Description']}\nSAP type/length: {typ}/{ln}\nAllowed SAP values: {allowed}\n"
                    f"Source (Oracle) values to translate: {todo}\n"
                    'Return {"map":{"<source>":"<sap value>" or null},"confidence":0-1,"notes":[...]}. '
                    "Use null when the correct SAP value cannot be known from the information given; never guess.")
            try:
                out = extract_json(llm.complete(SYSTEM, user, 2000))
                for v in todo:
                    cache.setdefault(f, {})[v] = {"to": (out.get("map") or {}).get(v), "conf": float(out.get("confidence", 0) or 0),
                                                  "notes": "; ".join(str(n) for n in out.get("notes", []))}
            except Exception as ex:  # noqa: BLE001
                for v in todo:
                    cache.setdefault(f, {})[v] = {"to": None, "conf": 0.0, "notes": f"AI error: {ex}"}
                    # failures are not persisted below
        for v in bad:
            c = cache[f][v]
            to = c["to"]
            ok = (to not in (None, "") and c["conf"] >= min_conf and (allowed is None or str(to).upper() in allowed)
                  and not (sized and len(str(to)) > int(ln)))
            if ok:
                maps.setdefault(f, {})[v] = str(to)
            decisions.append({"SAP Field": f, "Source value": v, "AI proposed": to, "Confidence": c["conf"],
                              "Applied": "YES" if ok else "NO - needs human", "Notes": c["notes"]})
    for f in list(cache):                                           # never persist AI errors
        cache[f] = {v: c for v, c in cache[f].items() if not str(c["notes"]).startswith("AI error")}
    return maps, decisions
