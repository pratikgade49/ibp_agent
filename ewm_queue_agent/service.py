"""Fetch and interpret the custom EWM queue monitoring OData service."""
import os
from urllib.parse import urljoin, urlsplit

import requests

from agent.s4_capacity_tools import _s4_request_config

SERVICE_ROOT = "/sap/opu/odata/sap/ZEWM_MSG_QUEUE_MON_SRV"
MAX_QUEUES = 1000


def diagnose(row):
    """Offer checks as hypotheses; only SAP's message is an observed cause."""
    error = str(row.get("Errmess") or "").lower()
    state = str(row.get("Qstate") or "").upper()
    direction = str(row.get("Mtypetext") or row.get("Direction") or "").lower()
    transaction = "SMQ2" if "inbound" in direction or row.get("Msgtype") == "I" else "SMQ1" if "outbound" in direction or row.get("Msgtype") == "O" else "SMQ1 or SMQ2"
    guidance = []
    if row.get("Wqname"):
        guidance.append(f"Check predecessor queue {row['Wqname']} and resolve its blockage first.")
    if "delivery type cannot be determined" in error:
        guidance.append("Check delivery type determination and mapping for this delivery and warehouse with the EWM and delivery configuration teams.")
        guidance.append("Verify the referenced delivery and its relevant organizational and master data in both systems.")
    elif any(word in error for word in ("material", "product", "master data")):
        guidance.append("Check that the referenced product and required warehouse master data exist and are consistent in both systems.")
    elif error:
        guidance.append("Use the SAP message class and number to investigate the error and check the referenced delivery and required master data.")
    else:
        guidance.append("Inspect the queue entry and application log in SAP for the detailed reason; no error text was provided by the service.")
    if state == "SYSFAIL":
        guidance.append("After correcting the underlying issue, an authorized SAP user can review and reprocess the queue using the appropriate SAP procedure.")
    else:
        guidance.append("Check the queue state and any dependencies in SAP before taking action.")
    return {**row, "transaction": transaction, "guidance": guidance}


def fetch_queues(warehouse="", date_from=None, date_to=None):
    base_url, options = _s4_request_config()
    root = os.environ.get("EWM_QUEUE_SERVICE_ROOT", SERVICE_ROOT).strip().rstrip("/")
    if not root.startswith("/") or root.startswith("//"):
        raise RuntimeError("EWM_QUEUE_SERVICE_ROOT must be a relative service path")
    url = f"{base_url.rstrip('/')}{root}/MsgQueueSet"
    params = {"$format": "json", "$top": str(MAX_QUEUES + 1)}
    filters = []
    if warehouse:
        filters.append("Lgnum eq '" + warehouse.replace("'", "''") + "'")
    if date_from and date_to and date_from == date_to:
        filters.append(f"Creadate eq datetime'{date_from.isoformat()}T00:00:00'")
    else:
        if date_from:
            filters.append(f"Creadate ge datetime'{date_from.isoformat()}T00:00:00'")
        if date_to:
            filters.append(f"Creadate le datetime'{date_to.isoformat()}T23:59:59'")
    if filters:
        params["$filter"] = " and ".join(filters)
    origin = urlsplit(url)
    visited = set()
    rows = []
    while url:
        if url in visited:
            raise RuntimeError("SAP returned a repeated queue page link")
        visited.add(url)
        response = requests.get(url, params=params, **options)
        params = None
        if not response.ok:
            raise RuntimeError(f"SAP queue service returned HTTP {response.status_code}")
        try:
            data = response.json()["d"]
            page = data["results"]
            if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
                raise ValueError()
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("SAP queue service returned an invalid OData response") from exc
        rows.extend(page)
        next_link = data.get("__next")
        if len(rows) > MAX_QUEUES:
            break
        if next_link:
            next_url = urljoin(url, next_link)
            parsed = urlsplit(next_url)
            if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
                raise RuntimeError("SAP queue service returned an unsafe page link")
            url = next_url
        else:
            url = None
    truncated = len(rows) > MAX_QUEUES or bool(next_link)
    rows = rows[:MAX_QUEUES]
    return {"queues": [diagnose(row) for row in rows], "count": len(rows), "truncated": truncated}
