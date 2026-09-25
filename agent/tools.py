"""
Tool functions for the IBP Demand Planning Agent Suite.
Each function is a plain Python callable the LangGraph agent can invoke.
By default they run in MOCK mode (no live IBP tenant needed) using fixture
data lifted directly from the original Joule Studio design doc's test
prompts, so you can build and demo the full agent before wiring in a real
SAP IBP system.
Set USE_MOCK_DATA=false in the environment once you have a real IBP
destination configured, and the same functions will call the live
PLANNING_DATA_API_SRV OData service instead.
"""
import os
import re
import statistics
import uuid
from datetime import datetime
from typing import Optional
import requests
USE_MOCK_DATA = os.environ.get("USE_MOCK_DATA", "true").lower() == "true"
# ---------------------------------------------------------------------------
# Destination / auth helper
# ---------------------------------------------------------------------------
# On Cloud Foundry, destination + credentials are resolved via the
# Destination service binding (VCAP_SERVICES) rather than hardcoded here.
# This keeps parity with the original doc's SAP_IBP_DESTINATION concept
# (Basic Auth, Internet proxy type) without pulling in the full SAP Cloud
# SDK — swap this helper for `sap.destination` client calls later if you
# want native BTP destination resolution instead of manual env vars.
IBP_BASE_URL = os.environ.get("IBP_BASE_URL", "")
IBP_USER = os.environ.get("IBP_USER", "")
IBP_PASSWORD = os.environ.get("IBP_PASSWORD", "")
IBP_PLANNING_AREA = os.environ.get("IBP_PLANNING_AREA", "ZJPIBP1")
IBP_PERIOD_LEVEL = os.environ.get("IBP_PERIOD_LEVEL", "3")
IBP_UOM_TO_ID = os.environ.get("IBP_UOM_TO_ID", "EA")
IBP_KEY_FIGURE = os.environ.get("IBP_KEY_FIGURE", "STATISTICALFORECASTQTY")
IBP_TRANSACTION_NAME = os.environ.get("IBP_TRANSACTION_NAME", "IBP Demand Agent")
# Standard IBP capacity key figures by resource type. Planning-area dimensions
# remain configurable for each tenant.
CAPACITY_KEY_FIGURES = {
    "handling": ("CAPADEMAND", "CAPAUSAGE"),
    "storage": ("CAPADEMAND", "CAPAUSAGE"),
    "production": ("PCAPADEMAND", "PCAPAUSAGE"),
    "transportation": ("TCAPADEMAND", "TCAPAUSAGE"),
}
CAPACITY_RATE_KEY_FIGURES = {
    "handling": "CAPACONSUMPTION",
    "storage": "CAPACONSUMPTION",
    "production": "PCAPACONSUMPTION",
    "transportation": "TCAPACONSUMPTION",
}
CAPACITY_UTILIZATION_FIELD_CANDIDATES = {
    "handling": (os.environ.get("IBP_CAPACITY_UTILIZATION_FIELD") or "UTILIZATIONPCT",),
    "storage": (os.environ.get("IBP_CAPACITY_UTILIZATION_FIELD") or "UTILIZATIONPCT",),
    "production": (os.environ.get("IBP_CAPACITY_UTILIZATION_FIELD") or "UTILIZATIONPCT",),
    "transportation": (os.environ.get("IBP_CAPACITY_UTILIZATION_FIELD") or "UTILIZATIONPCT",),
}
CAPACITY_SUPPLY_KEY_FIGURE = "CAPASUPPLY"
CAPACITY_RESOURCE_FIELD = os.environ.get("IBP_CAPACITY_RESOURCE_FIELD", "RESID")
CAPACITY_SOURCE_FIELD = os.environ.get("IBP_CAPACITY_SOURCE_FIELD", "SOURCEID")
CAPACITY_SHIP_FROM_FIELD = os.environ.get("IBP_CAPACITY_SHIP_FROM_FIELD") or ""
CAPACITY_MODE_FIELD = os.environ.get("IBP_CAPACITY_MODE_FIELD") or ""
CAPACITY_TRANSPORT_SUPPLY_LOCATION = os.environ.get("IBP_CAPACITY_TRANSPORT_SUPPLY_LOCATION") or ""
CAPACITY_SOURCE_TYPES = {
    value.strip().lower()
    for value in os.environ.get("IBP_CAPACITY_SOURCE_TYPES", "production").split(",")
    if value.strip()
}
IBP_NAVIGATION_PROPERTY = os.environ.get(
    "IBP_NAVIGATION_PROPERTY", f"Nav{IBP_PLANNING_AREA}"
)
PLANNING_DATA_PATH = (
    "/sap/opu/odata/IBP/PLANNING_DATA_API_SRV/"
    f"{IBP_PLANNING_AREA}"
)
IBP_SERVICE_ROOT = "/sap/opu/odata/IBP/PLANNING_DATA_API_SRV"
MASTER_DATA_SERVICE_ROOT = "/sap/opu/odata/IBP/MASTER_DATA_API_SRV"
MASTER_DATA_TRANSACTION_NAME = os.environ.get(
    "MASTER_DATA_TRANSACTION_NAME", "IBP Demand Agent Master Data"
)

def _period_month(period_id) -> str | None:
    """Convert SAP OData date values to a YYYY-MM display value."""
    if not period_id:
        return None
    if isinstance(period_id, str):
        match = re.search(r"/Date\((\d+)", period_id)
        if match:
            return datetime.utcfromtimestamp(int(match.group(1)) / 1000).strftime("%Y-%m")
        if len(period_id) >= 7 and period_id[4] == "-":
            return period_id[:7]
    return None

def _months_between(start_period: str, end_period: str) -> list[str]:
    """Return inclusive YYYY-MM periods between two valid month values."""
    start = datetime.strptime(start_period, "%Y-%m")
    end = datetime.strptime(end_period, "%Y-%m")
    periods = []
    current = start
    while current <= end:
        periods.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return periods

def _display_id(value: str | None) -> str | None:
    """Extract an ID from a planning-table value such as 'ID - Description'."""
    return value.split(" - ", 1)[0].strip() if value else None

def _product_id_candidates(product: str | None) -> list[str | None]:
    product_id = _display_id(product)
    if not product_id:
        return [None]
    candidates = [product_id]
    alternate = product_id.replace("-", "_") if "-" in product_id else product_id.replace("_", "-")
    if alternate != product_id:
        candidates.append(alternate)
    return candidates

def _ibp_get(select: str, filter_: str) -> dict:
    """Shared GET against the IBP PlanningData OData collection."""
    if "UOMTOID" in select and "UOMTOID" not in filter_:
            filter_ = f"({filter_}) and UOMTOID eq '{IBP_UOM_TO_ID}'"
    url = f"{IBP_BASE_URL}{PLANNING_DATA_PATH}"
    params = {"$select": select, "$filter": filter_, "$format": "json"}
    resp = requests.get(
        url, params=params, auth=(IBP_USER, IBP_PASSWORD), timeout=30
    )
    if not resp.ok:
        raise RuntimeError(
            f"SAP IBP request failed with HTTP {resp.status_code}; "
            f"select={select}; filter={filter_}; response={resp.text}"
        )
    return resp.json()


def _period_timestamp(period: str) -> str:
    """Convert a YYYY-MM period to the timestamp expected by the import API."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        raise ValueError("period must use YYYY-MM format")
    return f"{period}-01T00:00:00"


def _ibp_write(payload: dict) -> dict:
    """Import one planning record through the documented Trans API."""
    transaction_url = (
        f"{IBP_BASE_URL}{PLANNING_DATA_PATH}Trans"
    )
    service_root = f"{IBP_BASE_URL}{IBP_SERVICE_ROOT}"
    session = requests.Session()
    token_response = session.get(
        f"{service_root}/$metadata",
        headers={"x-csrf-token": "fetch", "Accept": "application/xml"},
        auth=(IBP_USER, IBP_PASSWORD),
        timeout=30,
    )
    if not token_response.ok:
        raise RuntimeError(
            f"SAP IBP CSRF token request failed with HTTP {token_response.status_code}: "
            f"{token_response.text}"
        )
    csrf_token = token_response.headers.get("x-csrf-token")
    if not csrf_token:
        raise RuntimeError("SAP IBP did not return an x-csrf-token")
    response = session.post(
        transaction_url,
        json=payload,
        headers={
            "x-csrf-token": csrf_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        auth=(IBP_USER, IBP_PASSWORD),
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"SAP IBP import failed with HTTP {response.status_code}: {response.text}"
        )
    return {
        "status": "import_submitted",
        "method": "POST",
        "endpoint": transaction_url,
        "planning_area": IBP_PLANNING_AREA,
        "transaction_id": payload["Transactionid"],
        "payload": payload,
        "response": response.json() if response.content else {},
    }


def update_planning_data(
    product: str | None = None,
    location: str | None = None,
    period: str | None = None,
    forecast: float | None = None,
    customer: str | None = None,
    uom: str | None = None,
    version: str | None = None,
    aggregation_fields: list[str] | None = None,
    aggregation_values: dict[str, str | int | float] | list[dict[str, str]] | None = None,
    confirm: bool = False,
) -> dict:
    """Import one key-figure row with a caller-defined planning level."""
    if aggregation_fields is not None or aggregation_values is not None:
        if not aggregation_fields or not aggregation_values:
            raise ValueError("aggregation_fields and aggregation_values are both required")
        if isinstance(aggregation_values, list):
            aggregation_values = {
                item["field"]: item["value"] for item in aggregation_values
            }
        if set(aggregation_fields) != set(aggregation_values):
            raise ValueError("aggregation_fields must exactly match aggregation_values keys")
        if len(set(aggregation_fields)) != len(aggregation_fields):
            raise ValueError("aggregation_fields must not contain duplicates")
        fields = list(aggregation_fields)
        row = dict(aggregation_values)
        for field in fields:
            if field.endswith("_TSTAMP") and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(row[field])):
                row[field] = _period_timestamp(str(row[field]))
    else:
        if not product or not period or forecast is None:
            raise ValueError("product, period, and forecast are required without aggregation_values")
        if not location and not customer:
            raise ValueError("location or customer is required")
        row = {
            "PRDID": _display_id(product),
            f"PERIODID{IBP_PERIOD_LEVEL}_TSTAMP": _period_timestamp(period),
            IBP_KEY_FIGURE: str(forecast),
        }
        fields = []
        if location:
            row["LOCID"] = _display_id(location)
            fields.append("LOCID")
        if customer:
            row["CUSTID"] = _display_id(customer)
            fields.append("CUSTID")
        fields.extend(["PRDID", IBP_KEY_FIGURE, f"PERIODID{IBP_PERIOD_LEVEL}_TSTAMP"])
    if not confirm:
        return {
            "status": "confirmation_required",
            "message": (
                f"This will import fields {', '.join(fields)} with values {row}. "
                "Ask the user to confirm before writing."
            ),
        }
    transaction_payload = {
        "Transactionid": uuid.uuid4().hex,
        "AggregationLevelFieldsString": ",".join(fields),
        "DoCommit": True,
        "TransactionName": IBP_TRANSACTION_NAME,
        IBP_NAVIGATION_PROPERTY: [row],
    }
    if version:
        transaction_payload["VersionID"] = version
    return _ibp_write(transaction_payload)


def _master_data_write(master_data_type: str, payload: dict) -> dict:
    """Import master-data records through the documented Trans entity."""
    service_root = f"{IBP_BASE_URL}{MASTER_DATA_SERVICE_ROOT}"
    transaction_url = f"{service_root}/{master_data_type}Trans"
    session = requests.Session()
    token_response = session.get(
        f"{service_root}/$metadata",
        headers={"x-csrf-token": "fetch", "Accept": "application/xml"},
        auth=(IBP_USER, IBP_PASSWORD),
        timeout=30,
    )
    if not token_response.ok:
        raise RuntimeError(
            f"SAP IBP master-data CSRF request failed with HTTP "
            f"{token_response.status_code}: {token_response.text}"
        )
    csrf_token = token_response.headers.get("x-csrf-token")
    if not csrf_token:
        raise RuntimeError("SAP IBP did not return an x-csrf-token")
    response = session.post(
        transaction_url,
        json=payload,
        headers={
            "x-csrf-token": csrf_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        auth=(IBP_USER, IBP_PASSWORD),
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"SAP IBP master-data import failed with HTTP "
            f"{response.status_code}: {response.text}"
        )
    return {
        "status": "import_submitted",
        "method": "POST",
        "endpoint": transaction_url,
        "master_data_type": master_data_type,
        "response": response.json() if response.content else {},
    }


def import_master_data(
    master_data_type: str,
    requested_attributes: list[str],
    records: list[dict],
    planning_area: str | None = None,
    version: str | None = None,
    delete_entries: bool = False,
    confirm: bool = False,
) -> dict:
    """Create, modify, or delete master data after explicit confirmation."""
    master_data_type = master_data_type.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", master_data_type):
        raise ValueError("master_data_type must be a valid SAP entity name")
    if not requested_attributes:
        raise ValueError("requested_attributes must not be empty")
    if not records or len(records) > 5000:
        raise ValueError("records must contain between 1 and 5000 items")
    if not confirm:
        operation = "delete" if delete_entries else "import"
        return {
            "status": "confirmation_required",
            "message": (
                f"This will {operation} {len(records)} record(s) for "
                f"master data type {master_data_type}. Ask the user to confirm "
                "before writing."
            ),
        }
    payload = {
        "TransactionID": uuid.uuid4().hex,
        "RequestedAttributes": ",".join(requested_attributes),
        "DoCommit": True,
        "TransactionName": MASTER_DATA_TRANSACTION_NAME,
        f"Nav{master_data_type}": records,
    }
    if planning_area:
        payload["PlanningAreaID"] = planning_area
    if version:
        payload["VersionID"] = version
    if delete_entries:
        payload["DeleteEntries"] = True
    return _master_data_write(master_data_type, payload)


def recommend_planning_action(
    issue_type: str,
    product: str,
    location: str | None = None,
    period: str | None = None,
    forecast: float | None = None,
    actual: float | None = None,
    anomaly_type: str | None = None,
    anomaly_period: str | None = None,
) -> dict:
    """Create an explainable recommendation without changing SAP IBP data."""
    allowed_issue_types = {
        "over_consumption",
        "under_consumption",
        "spike",
        "drop",
        "flatline",
    }
    if issue_type not in allowed_issue_types:
        raise ValueError(f"issue_type must be one of {sorted(allowed_issue_types)}")
    if forecast is not None and forecast < 0:
        raise ValueError("forecast cannot be negative")
    if actual is not None and actual < 0:
        raise ValueError("actual cannot be negative")

    recommendation = {
        "product": product,
        "location": location,
        "period": period or anomaly_period,
        "issue_type": issue_type,
        "action": "investigate",
        "proposed_forecast": None,
        "requires_confirmation": False,
    }
    if issue_type == "over_consumption":
        recommendation.update(
            {
                "summary": "Actual consumption is above the statistical forecast.",
                "actions": [
                    "Check whether the increase is driven by a customer order or promotion.",
                    "Validate inventory and supply constraints before increasing the forecast.",
                    "Review the next planning periods for a recurring pattern.",
                ],
            }
        )
    elif issue_type == "under_consumption":
        recommendation.update(
            {
                "summary": "Actual consumption is below the statistical forecast.",
                "actions": [
                    "Check for cancellations, stock-outs, or delayed consumption postings.",
                    "Validate whether the lower demand is temporary or recurring.",
                    "Review customer and location segmentation before reducing the forecast.",
                ],
            }
        )
    elif issue_type == "spike":
        recommendation.update(
            {
                "summary": "A sudden upward movement was detected in the forecast pattern.",
                "actions": [
                    "Verify the underlying demand signal and source data.",
                    "Check for a one-time order or promotion before propagating the increase.",
                    "Use a business-approved adjustment only after the spike is validated.",
                ],
            }
        )
    elif issue_type == "drop":
        recommendation.update(
            {
                "summary": "A sudden downward movement was detected in the forecast pattern.",
                "actions": [
                    "Check for missing data, supply disruption, or a demand cancellation.",
                    "Confirm the drop is not caused by a period or unit-of-measure issue.",
                    "Review the next periods before applying a forecast reduction.",
                ],
            }
        )
    else:
        recommendation.update(
            {
                "summary": "The forecast has remained unchanged across multiple periods.",
                "actions": [
                    "Check whether the series is intentionally fixed or missing refreshed inputs.",
                    "Validate the planning job and source data load status.",
                    "Do not adjust the forecast until the flatline cause is understood.",
                ],
            }
        )

    if forecast is not None and actual is not None and forecast > 0:
        variance_pct = (actual - forecast) / forecast * 100
        recommendation["variance_pct"] = round(variance_pct, 1)
        recommendation["proposed_forecast"] = round(actual, 2)
        recommendation["action"] = "review_and_confirm_forecast_change"
        recommendation["requires_confirmation"] = True
        recommendation["change_note"] = (
            f"Review changing the forecast from {forecast} to {actual}; "
            "this is a proposal only and has not been written to SAP IBP."
        )
    elif forecast == 0 and actual not in (None, 0):
        recommendation["variance_pct"] = None
        recommendation["change_note"] = (
            "A percentage variance is not calculable because the forecast is zero. "
            "Validate the baseline before proposing a numeric change."
        )
    return recommendation

# ---------------------------------------------------------------------------
# 1. Forecast vs. Consumption Alert
# ---------------------------------------------------------------------------
_MOCK_FORECAST_VS_CONSUMPTION = {
   ("1010", "Product A"): {"forecast": 10000, "actual": 13500},
}

def _forecast_consumption_rows(
    location: str | None = None,
    product: str | None = None,
    customer: str | None = None,
    period_start_rel: int = 0,
    period_end_rel: int = 0,
    ) -> list[dict]:
    """Load forecast and actual rows for deterministic analytics."""
    if period_start_rel > period_end_rel:
        raise ValueError("period_start_rel must not exceed period_end_rel")
    if USE_MOCK_DATA:
        return [
            {
                "location": row_location,
                "product": row_product,
                "customer": None,
                "period": None,
                **values,
            }
            for (row_location, row_product), values in _MOCK_FORECAST_VS_CONSUMPTION.items()
            if (location is None or row_location == location)
            and (product is None or row_product == product)
            and customer is None
        ]

    filters = [
        f"UOMTOID eq '{IBP_UOM_TO_ID}'",
        f"PERIODID{IBP_PERIOD_LEVEL}_REL ge {period_start_rel}",
        f"PERIODID{IBP_PERIOD_LEVEL}_REL le {period_end_rel}",
    ]
    if location:
        filters.append(f"LOCID eq '{location}'")
    if product:
        filters.append(f"PRDID eq '{product}'")
    if customer:
        filters.append(f"CUSTID eq '{customer}'")
    result = _ibp_get(
        select=(
            f"PRDID,LOCID,CUSTID,PERIODID{IBP_PERIOD_LEVEL}_TSTAMP,"
            "UOMTOID,STATISTICALFORECASTQTY,ACTUALSQTY"
        ),
        filter_=" and ".join(filters),
    )
    return [
        {
            "location": row["LOCID"],
            "product": row["PRDID"],
            "customer": row.get("CUSTID"),
            "period": _period_month(row.get(f"PERIODID{IBP_PERIOD_LEVEL}_TSTAMP")),
            "forecast": float(row["STATISTICALFORECASTQTY"]),
            "actual": float(row["ACTUALSQTY"]),
        }
        for row in result.get("d", {}).get("results", [])
    ]

def query_planning_data(
    metric: str = "forecast",
    aggregation: str = "sum",
    group_by: list[str] | None = None,
    location: str | None = None,
    product: str | None = None,
    customer: str | None = None,
    period_start_rel: int = 0,
    period_end_rel: int = 0,
    threshold_pct: float | None = None,
    sort: str = "desc",
    limit: int = 10,
    ) -> dict:
    """Run validated, deterministic analytics over forecast/actual rows."""
    allowed_metrics = {"forecast", "actual", "variance_qty", "variance_pct"}
    allowed_aggregations = {"sum", "max", "min", "avg"}
    allowed_dimensions = {"product", "location", "customer", "period"}
    if metric not in allowed_metrics:
        raise ValueError(f"metric must be one of {sorted(allowed_metrics)}")
    if aggregation not in allowed_aggregations:
        raise ValueError(f"aggregation must be one of {sorted(allowed_aggregations)}")
    group_by = group_by or []
    if any(dimension not in allowed_dimensions for dimension in group_by):
        raise ValueError(f"group_by must contain only {sorted(allowed_dimensions)}")
    if sort not in {"asc", "desc"}:
        raise ValueError("sort must be 'asc' or 'desc'")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    rows = _forecast_consumption_rows(
        location=location,
        product=product,
        customer=customer,
        period_start_rel=period_start_rel,
        period_end_rel=period_end_rel,
    )
    if threshold_pct is not None:
        rows = [
            row for row in rows
            if row["forecast"]
            and abs((row["actual"] - row["forecast"]) / row["forecast"] * 100)
            > threshold_pct
        ]

    def value(row: dict) -> float:
        if metric == "forecast":
            return row["forecast"]
        if metric == "actual":
            return row["actual"]
        variance = row["actual"] - row["forecast"]
        if metric == "variance_qty":
            return variance
        return (variance / row["forecast"] * 100) if row["forecast"] else 0.0

    groups: dict[tuple, list[float]] = {}
    for row in rows:
        key = tuple(row.get(dimension) for dimension in group_by)
        groups.setdefault(key, []).append(value(row))
    results = []
    for key, values in groups.items():
        if aggregation == "sum":
            aggregate = sum(values)
        elif aggregation == "max":
            aggregate = max(values)
        elif aggregation == "min":
            aggregate = min(values)
        else:
            aggregate = sum(values) / len(values)
        result = {dimension: key[index] for index, dimension in enumerate(group_by)}
        if metric in {"variance_qty", "variance_pct"}:
            grouped_rows = [
                row for row in rows
                if tuple(row.get(dimension) for dimension in group_by) == key
            ]
            forecast_total = sum(row["forecast"] for row in grouped_rows)
            actual_total = sum(row["actual"] for row in grouped_rows)
            result.update(
                {
                    "forecast": round(forecast_total, 2),
                    "actual": round(actual_total, 2),
                    "variance_qty": round(actual_total - forecast_total, 2),
                    "variance_pct": round(
                        (actual_total - forecast_total) / forecast_total * 100,
                        2,
                    )
                    if forecast_total
                    else None,
                }
            )
        else:
            result[metric] = round(aggregate, 2)
        results.append(result)
    results.sort(
        key=lambda result: result[metric],
        reverse=sort == "desc",
    )
    return {
        "metric": metric,
        "aggregation": aggregation,
        "group_by": group_by,
        "filters": {
            "location": location,
            "product": product,
            "customer": customer,
            "period_start_rel": period_start_rel,
            "period_end_rel": period_end_rel,
            "threshold_pct": threshold_pct,
        },
        "total_rows_evaluated": len(rows),
        "results": results[:limit],
    }

def get_forecast_vs_consumption(
    location: str | None = None,
    product: str | None = None,
        customer: str | None = None,
    threshold_pct: float = 20.0,
        result_scope: str = "combination",
        alert_direction: str = "both",
        period_start_rel: int = 0,
        period_end_rel: int = 0,
    ) -> dict:
    """
    Compare statistical forecast vs actual consumption. Location, product, and
    customer are optional. Use result_scope="product" for product totals or
    result_scope="combination" for product/location/customer detail.
    """
    if result_scope not in {"product", "combination"}:
        raise ValueError("result_scope must be 'product' or 'combination'")
    if alert_direction not in {"over", "under", "both"}:
        raise ValueError("alert_direction must be 'over', 'under', or 'both'")
    if period_start_rel > period_end_rel:
        raise ValueError("period_start_rel must not exceed period_end_rel")
    analyses = []
    if USE_MOCK_DATA:
        rows = [
            {"location": row_location, "product": row_product, **values}
            for (row_location, row_product), values in _MOCK_FORECAST_VS_CONSUMPTION.items()
            if (location is None or row_location == location)
            and (product is None or row_product == product)
            and customer is None
        ]
    else:
        filters = [
            f"UOMTOID eq '{IBP_UOM_TO_ID}'",
            f"PERIODID{IBP_PERIOD_LEVEL}_REL ge {period_start_rel}",
            f"PERIODID{IBP_PERIOD_LEVEL}_REL le {period_end_rel}",
        ]
        if location:
            filters.append(f"LOCID eq '{location}'")
        if product:
            filters.append(f"PRDID eq '{product}'")
        if customer:
            filters.append(f"CUSTID eq '{customer}'")
        result = _ibp_get(
            select=(
                f"PRDID,LOCID,CUSTID,PERIODID{IBP_PERIOD_LEVEL}_TSTAMP,"
                "UOMTOID,STATISTICALFORECASTQTY,ACTUALSQTY"
            ),
            filter_=" and ".join(filters),
        )
        rows = [
            {
                "location": row["LOCID"],
                "product": row["PRDID"],
                "customer": row.get("CUSTID"),
                "period": _period_month(row.get(f"PERIODID{IBP_PERIOD_LEVEL}_TSTAMP")),
                "forecast": float(row["STATISTICALFORECASTQTY"]),
                "actual": float(row["ACTUALSQTY"]),
            }
            for row in result.get("d", {}).get("results", [])
        ]
    if result_scope == "product":
        grouped = {}
        for row in rows:
            group_key = (row["product"], row.get("period"))
            grouped.setdefault(
                group_key,
                {
                    "product": row["product"],
                    "period": row.get("period"),
                    "forecast": 0.0,
                    "actual": 0.0,
                },
            )
            grouped[group_key]["forecast"] += row["forecast"]
            grouped[group_key]["actual"] += row["actual"]
        rows = list(grouped.values())
    for row in rows:
        forecast, actual = row["forecast"], row["actual"]
        variance_pct = ((actual - forecast) / forecast) * 100 if forecast else None
        direction = "over-consumption" if actual > forecast else "under-consumption"
        direction_matches = (
            alert_direction == "both"
            or (alert_direction == "over" and direction == "over-consumption")
            or (alert_direction == "under" and direction == "under-consumption")
        )
        analyses.append(
            {
                **row,
                "variance_pct": round(variance_pct, 1) if variance_pct is not None else None,
                "alert": (
                    direction_matches
                    and (
                        (variance_pct is not None and abs(variance_pct) > threshold_pct)
                        or (forecast == 0 and actual != 0)
                    )
                ),
                "direction": direction,
                "variance_status": (
                    "not-calculable-zero-forecast"
                    if forecast == 0
                    else "calculated"
                ),
            }
        )
    response = {
        "location_filter": location,
        "product_filter": product,
        "customer_filter": customer,
        "result_scope": result_scope,
        "threshold_pct": threshold_pct,
            "alert_direction": alert_direction,
            "period_start_rel": period_start_rel,
            "period_end_rel": period_end_rel,
        "count": len(analyses),
        "alert_count": sum(item["alert"] for item in analyses),
        "alert_results": [
            {
                key: item[key]
                for key in ("product", "location", "customer", "period", "forecast", "actual", "variance_pct", "variance_status", "direction")
                if key in item
            }
            for item in analyses
            if item["alert"]
        ],
    }
    if len(analyses) == 1:
        response.update(analyses[0])
    return response

# ---------------------------------------------------------------------------
# 2. IBP Capacity Bottleneck Resolver
# ---------------------------------------------------------------------------
_MOCK_CAPACITY_ROWS = [
    {
        "resource_type": "production", "resource": "PRESS-01", "location": "PLANT-A",
        "product": "FG-100", "source": "PROD-SOURCE-1", "period": "2026-10",
        "supply_location": "PLANT-A", "ship_from_location": None, "mode_of_transport": None,
        "consumption_rate": 1.0, "is_supply_row": False, "demand": 120.0, "usage": 120.0, "supply": 100.0,
    },
    {
        "resource_type": "storage", "resource": "WH-A", "location": "PLANT-A",
        "product": "FG-100", "source": None, "period": "2026-10",
        "supply_location": "PLANT-A", "ship_from_location": None, "mode_of_transport": None,
        "consumption_rate": 1.0, "is_supply_row": False, "demand": 80.0, "usage": 75.0, "supply": 100.0,
    },
]

def analyze_capacity_bottlenecks(
    resource_type: str = "all",
    resource: str | None = None,
    location: str | None = None,
    product: str | None = None,
    period_start_rel: int = 0,
    period_end_rel: int = 3,
    utilization_threshold_pct: float = 80.0,
) -> dict:
    """Find IBP resource-period capacity shortages and high utilization."""
    aliases = {
        "handling": "handling", "handling resource": "handling",
        "storage": "storage", "storage resource": "storage",
        "production": "production", "production resource": "production",
        "transportation": "transportation", "transport": "transportation",
        "transportation resource": "transportation", "all": "all",
    }
    normalized_type = aliases.get(resource_type.strip().lower())
    if normalized_type is None:
        raise ValueError("resource_type must be handling, production, storage, transportation, or all")
    if period_start_rel > period_end_rel:
        raise ValueError("period_start_rel must not exceed period_end_rel")
    if utilization_threshold_pct < 0:
        raise ValueError("utilization_threshold_pct cannot be negative")

    if normalized_type == "all":
        all_results = []
        total_rows_evaluated = 0
        for current_type in CAPACITY_KEY_FIGURES:
            if current_type == "transportation" and (not CAPACITY_SHIP_FROM_FIELD or not CAPACITY_MODE_FIELD):
                continue
            current_result = analyze_capacity_bottlenecks(
                resource_type=current_type,
                resource=resource,
                location=location,
                product=product,
                period_start_rel=period_start_rel,
                period_end_rel=period_end_rel,
                utilization_threshold_pct=utilization_threshold_pct,
            )
            all_results.extend(current_result.get("results", []))
            total_rows_evaluated += current_result.get("total_rows_evaluated", 0)

        def dedupe_key(item: dict) -> tuple:
            return (
                item.get("resource_type"),
                item.get("resource"),
                item.get("location"),
                item.get("product"),
                item.get("source"),
                item.get("ship_from_location"),
                item.get("mode_of_transport"),
                item.get("period"),
            )

        deduped = []
        seen = set()
        for item in all_results:
            key = dedupe_key(item)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        tables = {}
        for current_type in CAPACITY_KEY_FIGURES:
            if current_type == "transportation" and (not CAPACITY_SHIP_FROM_FIELD or not CAPACITY_MODE_FIELD):
                continue
            type_rows = [item for item in deduped if item.get("resource_type") == current_type]
            tables[current_type] = {
                "resource_type": current_type,
                "title": f"{current_type.replace('_', ' ').title()} capacity",
                "key_figures": {
                    "demand": CAPACITY_KEY_FIGURES[current_type][0],
                    "usage": CAPACITY_KEY_FIGURES[current_type][1],
                    "supply": CAPACITY_SUPPLY_KEY_FIGURE,
                    "consumption_rate": CAPACITY_RATE_KEY_FIGURES[current_type],
                },
                "summary": {
                    "resource_period_count": len(type_rows),
                    "bottleneck_count": sum(item.get("status") == "High_Utilization" for item in type_rows),
                    "shortage_count": sum((item.get("shortage") or 0) > 0 for item in type_rows),
                },
                "rows": type_rows,
            }

        if resource is not None and len(deduped) > 1:
            merged = {
                "resource_type": "all",
                "resource": resource,
                "location": deduped[0].get("location"),
                "period": deduped[0].get("period"),
                "source": next((item.get("source") for item in deduped if item.get("source") is not None), None),
                "supply_location": next((item.get("supply_location") for item in deduped if item.get("supply_location") is not None), None),
                "ship_from_location": next((item.get("ship_from_location") for item in deduped if item.get("ship_from_location") is not None), None),
                "mode_of_transport": next((item.get("mode_of_transport") for item in deduped if item.get("mode_of_transport") is not None), None),
                "demand": sum(item.get("demand") or 0 for item in deduped),
                "usage": sum(item.get("usage") or 0 for item in deduped),
                "supply": max((item.get("supply") for item in deduped if item.get("supply") is not None), default=None),
                "contributors": [
                    contributor
                    for item in deduped
                    for contributor in item.get("contributors", [])
                ][:10],
            }
            merged["shortage"] = round(max(0.0, merged["demand"] - merged["supply"]), 2) if merged["supply"] is not None else None
            merged["headroom"] = round(merged["supply"] - merged["usage"], 2) if merged["supply"] is not None else None
            merged["utilization_pct"] = round((merged["usage"] / merged["supply"] * 100), 2) if merged["supply"] and merged["supply"] > 0 else None
            merged["status"] = (
                "High_Utilization" if merged["utilization_pct"] is not None and merged["utilization_pct"] > 100
                else "Within_Capacity"
            )
            deduped = [merged]

        combined = {
            "resource_type": "all",
            "key_figures": {
                current_type: {
                    "demand": CAPACITY_KEY_FIGURES[current_type][0],
                    "usage": CAPACITY_KEY_FIGURES[current_type][1],
                    "supply": CAPACITY_SUPPLY_KEY_FIGURE,
                    "consumption_rate": CAPACITY_RATE_KEY_FIGURES[current_type],
                }
                for current_type in CAPACITY_KEY_FIGURES
                if not (current_type == "transportation" and (not CAPACITY_SHIP_FROM_FIELD or not CAPACITY_MODE_FIELD))
            },
            "planning_levels": {
                "handling": ["resource", "location", "product"],
                "storage": ["resource", "location", "product"],
                "production": ["resource", "location", "product", "source_id"],
                "transportation": [
                    "product", "location", "ship_from_location",
                    "mode_of_transport", "resource",
                ],
                "capacity_supply": ["resource", "location"],
            },
            "filters": {
                "resource": resource, "location": location, "product": product,
                "period_start_rel": period_start_rel, "period_end_rel": period_end_rel,
                "utilization_threshold_pct": utilization_threshold_pct,
            },
            "total_rows_evaluated": total_rows_evaluated,
            "resource_period_count": len(deduped),
            "analysis_status": "complete" if deduped else "no_activity_data",
            "data_quality_warning": None,
            "activity_row_count": sum(1 for item in deduped if (item.get("demand") or 0) != 0 or (item.get("usage") or 0) != 0),
            "missing_value_count": 0,
            "bottleneck_count": sum(item["status"] == "High_Utilization" for item in deduped),
            "shortage_count": sum((item.get("shortage") or 0) > 0 for item in deduped),
            "bottlenecks": [item for item in deduped if item["status"] == "High_Utilization"],
            "results": deduped,
            "tables": tables,
        }
        return combined

    requested_types = [normalized_type]
    rows = []
    if USE_MOCK_DATA:
        rows = [
            row for row in _MOCK_CAPACITY_ROWS
            if row["resource_type"] in requested_types
            and (resource is None or row["resource"] == resource)
            and (location is None or row["location"] == location)
            and (product is None or row["product"] == product)
        ]
    else:
        period_field = f"PERIODID{IBP_PERIOD_LEVEL}_REL"
        timestamp_field = f"PERIODID{IBP_PERIOD_LEVEL}_TSTAMP"
        dimension_fields = [CAPACITY_RESOURCE_FIELD, "LOCID", "PRDID"]
        if any(current_type in CAPACITY_SOURCE_TYPES for current_type in requested_types):
            dimension_fields.append(CAPACITY_SOURCE_FIELD)
        if "transportation" in requested_types and CAPACITY_SHIP_FROM_FIELD and CAPACITY_MODE_FIELD:
            dimension_fields.extend([CAPACITY_SHIP_FROM_FIELD, CAPACITY_MODE_FIELD])
        key_figure_fields = {
            CAPACITY_SUPPLY_KEY_FIGURE,
            *(field for current_type in requested_types for field in (
                *CAPACITY_KEY_FIGURES[current_type],
                CAPACITY_RATE_KEY_FIGURES[current_type],
                *CAPACITY_UTILIZATION_FIELD_CANDIDATES.get(current_type, ()),
            )),
        }
        # Relative period fields are valid filter properties for this service,
        # but are not selectable properties in the Planning Data API.
        select_fields = ",".join(dict.fromkeys(
            dimension_fields + [timestamp_field] + sorted(key_figure_fields)
        ))
        filters = [
            f"UOMTOID eq '{IBP_UOM_TO_ID}'",
            f"{period_field} ge {period_start_rel}",
            f"{period_field} le {period_end_rel}",
        ]
        if resource:
            filters.append(f"{CAPACITY_RESOURCE_FIELD} eq '{_display_id(resource)}'")
        if location and "transportation" not in requested_types:
            filters.append(f"LOCID eq '{_display_id(location)}'")
        elif location and "transportation" in requested_types:
            filters.append(
                f"(LOCID eq '{CAPACITY_TRANSPORT_SUPPLY_LOCATION}' or "
                f"LOCID eq '{_display_id(location)}')"
            )
        if product:
            filters.append(f"PRDID eq '{_display_id(product)}'")
        result = _ibp_get(select=select_fields, filter_=" and ".join(filters))
        for raw in result.get("d", {}).get("results", []):
            for current_type in requested_types:
                demand_field, usage_field = CAPACITY_KEY_FIGURES[current_type]
                rate_field = CAPACITY_RATE_KEY_FIGURES[current_type]
                raw_has_type_data = any(
                    raw.get(field) is not None and str(raw.get(field)).strip() != ""
                    for field in (demand_field, usage_field, rate_field)
                )
                if not raw_has_type_data:
                    continue

                def number(field: str) -> float | None:
                    value = raw.get(field)
                    if value is None or str(value).strip() == "":
                        return None
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        return None
                raw_location = raw.get("LOCID")
                is_transport_supply_row = (
                    current_type == "transportation"
                    and raw_location == CAPACITY_TRANSPORT_SUPPLY_LOCATION
                )
                utilization_candidates = CAPACITY_UTILIZATION_FIELD_CANDIDATES.get(current_type, ("UTILIZATIONPCT",))
                utilization_pct = None
                for candidate in utilization_candidates:
                    candidate_value = raw.get(candidate)
                    if candidate_value is None or str(candidate_value).strip() == "":
                        continue
                    try:
                        utilization_pct = float(candidate_value)
                        break
                    except (TypeError, ValueError):
                        continue
                rows.append({
                    "resource_type": current_type,
                    "resource": raw.get(CAPACITY_RESOURCE_FIELD),
                    "location": None if is_transport_supply_row else raw_location,
                    "supply_location": (
                        CAPACITY_TRANSPORT_SUPPLY_LOCATION
                        if current_type == "transportation" else raw_location
                    ),
                    "product": raw.get("PRDID"),
                    "source": raw.get(CAPACITY_SOURCE_FIELD),
                    "ship_from_location": raw.get(CAPACITY_SHIP_FROM_FIELD),
                    "mode_of_transport": raw.get(CAPACITY_MODE_FIELD),
                    "period": _period_month(raw.get(timestamp_field)),
                    "demand": number(demand_field),
                    "usage": number(usage_field),
                    "supply": number(CAPACITY_SUPPLY_KEY_FIGURE),
                    "utilization_pct": utilization_pct,
                    "consumption_rate": number(rate_field),
                    "is_supply_row": is_transport_supply_row,
                })

    grouped = {}
    for row in rows:
        group_location = None if row["resource_type"] == "transportation" else row["location"]
        group_key = (
            row["resource_type"],
            row["resource"],
            group_location,
            row["period"],
        )
        product_value = None
        source_value = None
        ship_value = None
        mode_value = None
        if row["resource_type"] in {"handling", "storage"}:
            product_value = row["product"]
        elif row["resource_type"] == "production":
            source_value = row["source"]
        elif row["resource_type"] == "transportation":
            ship_value = row["ship_from_location"]
            mode_value = row["mode_of_transport"]
        group = grouped.setdefault(
            group_key,
            {
                "resource_type": row["resource_type"],
                "resource": row["resource"],
                "location": group_location,
                "product": product_value,
                "period": row["period"],
                "source": source_value,
                "supply_location": row["supply_location"],
                "ship_from_location": ship_value,
                "mode_of_transport": mode_value,
                "demand": 0.0,
                "usage": 0.0,
                "supply": row["supply"],
                "utilization_pct": row.get("utilization_pct"),
                "contributors": [],
                "missing_demand": False,
                "missing_usage": False,
                "missing_supply": row["supply"] is None,
            },
        )
        if row["demand"] is None and not row["is_supply_row"]:
            group["missing_demand"] = True
        elif row["demand"] is not None:
            group["demand"] += row["demand"]
        if row["usage"] is None and not row["is_supply_row"]:
            group["missing_usage"] = True
        elif row["usage"] is not None:
            group["usage"] += row["usage"]
        if group["supply"] is None and row["supply"] is not None:
            group["supply"] = row["supply"]
            group["missing_supply"] = False
        utilization_value = row.get("utilization_pct")
        if utilization_value is not None:
            group["utilization_pct"] = utilization_value
        if not row["is_supply_row"]:
            group["contributors"].append(row)

    results = []
    for group in grouped.values():
        supply = group["supply"]
        usage = group["usage"]
        demand = group["demand"]
        required_values_present = not any(
            (group["missing_demand"], group["missing_usage"], group["missing_supply"])
        )
        utilization_from_ibp = group.get("utilization_pct")
        computed = (
        usage / supply * 100
        if required_values_present and supply and supply > 0
        else None
        )
        utilization = computed if computed is not None else utilization_from_ibp
        shortage = max(0.0, demand - supply) if required_values_present else None
        contributors = sorted(
            group["contributors"],
            key=lambda item: (item["demand"] or 0, item["usage"] or 0),
            reverse=True,
        )
        total_demand = sum(item["demand"] or 0 for item in contributors)
        results.append({
            key: group[key]
            for key in (
                "resource_type", "resource", "location", "period", "source",
                "supply_location", "ship_from_location", "mode_of_transport",
                "demand", "usage", "supply",
            )
        } | {
            "shortage": round(shortage, 2) if shortage is not None else None,
            "headroom": round(supply - usage, 2) if required_values_present else None,
            "utilization_pct": round(utilization, 2) if utilization is not None else None,
            "status": (
                "data_unavailable" if not required_values_present
                else "High_Utilization" if utilization is not None and utilization > 100
                else "Within_Capacity"
            ),
            "contributors": [
                {
                    "product": item["product"],
                    "location": item["location"],
                    "ship_from_location": item["ship_from_location"],
                    "mode_of_transport": item["mode_of_transport"],
                    "demand": item["demand"],
                    "usage": item["usage"],
                    "consumption_rate": item["consumption_rate"],
                    "demand_share_pct": round(
                        (item["demand"] or 0) / total_demand * 100, 2
                    ) if total_demand else None,
                }
                for item in contributors[:10]
            ],
        })
    results.sort(key=lambda item: (item["shortage"] or 0, item["utilization_pct"] or 0), reverse=True)
    if resource is not None and len(results) > 1:
        merged = {
            "resource_type": "all",
            "resource": resource,
            "location": results[0].get("location"),
            "period": results[0].get("period"),
            "source": next((item.get("source") for item in results if item.get("source") is not None), None),
            "supply_location": next((item.get("supply_location") for item in results if item.get("supply_location") is not None), None),
            "ship_from_location": next((item.get("ship_from_location") for item in results if item.get("ship_from_location") is not None), None),
            "mode_of_transport": next((item.get("mode_of_transport") for item in results if item.get("mode_of_transport") is not None), None),
            "demand": sum(item.get("demand") or 0 for item in results),
            "usage": sum(item.get("usage") or 0 for item in results),
            "supply": max((item.get("supply") for item in results if item.get("supply") is not None), default=None),
            "contributors": [
                contributor
                for item in results
                for contributor in item.get("contributors", [])
            ][:10],
        }
        merged["shortage"] = round(max(0.0, merged["demand"] - merged["supply"]), 2) if merged["supply"] is not None else None
        merged["headroom"] = round(merged["supply"] - merged["usage"], 2) if merged["supply"] is not None else None
        merged["utilization_pct"] = round((merged["usage"] / merged["supply"] * 100), 2) if merged["supply"] and merged["supply"] > 0 else None
        merged["status"] = (
            "High_Utilization" if merged["utilization_pct"] is not None and merged["utilization_pct"] > 100
            else "Within_Capacity"
        )
        results = [merged]
    missing_value_count = sum(
        1 for item in results
        if item["status"] == "data_unavailable"
    )
    activity_row_count = sum(
        1 for item in results
        if (item["demand"] or 0) != 0 or (item["usage"] or 0) != 0
    )
    if missing_value_count:
        analysis_status = "incomplete_data"
        data_quality_warning = (
            "One or more capacity rows are missing demand, usage, or supply values; "
            "bottleneck conclusions are incomplete."
        )
    elif results and activity_row_count == 0:
        analysis_status = "no_activity_data"
        data_quality_warning = (
            "All returned demand and usage values are zero. This may indicate no "
            "capacity activity or that the capacity key figures are not populated "
            "at this planning level."
        )
    else:
        analysis_status = "complete"
        data_quality_warning = None
    table_rows = [
        item for item in results
        if item.get("resource_type") == normalized_type
    ]
    return {
        "resource_type": normalized_type,
        "key_figures": {
            current_type: {
                "demand": CAPACITY_KEY_FIGURES[current_type][0],
                "usage": CAPACITY_KEY_FIGURES[current_type][1],
                "supply": CAPACITY_SUPPLY_KEY_FIGURE,
                "consumption_rate": CAPACITY_RATE_KEY_FIGURES[current_type],
            }
            for current_type in requested_types
        },
        "planning_levels": {
            "handling": ["resource", "location", "product"],
            "storage": ["resource", "location", "product"],
            "production": ["resource", "location", "product", "source_id"],
            "transportation": [
                "product", "location", "ship_from_location",
                "mode_of_transport", "resource",
            ],
            "capacity_supply": ["resource", "location"],
        },
        "filters": {
            "resource": resource, "location": location, "product": product,
            "period_start_rel": period_start_rel, "period_end_rel": period_end_rel,
            "utilization_threshold_pct": utilization_threshold_pct,
        },
        "total_rows_evaluated": len(rows),
        "resource_period_count": len(results),
        "analysis_status": analysis_status,
        "data_quality_warning": data_quality_warning,
        "activity_row_count": activity_row_count,
        "missing_value_count": missing_value_count,
        "bottleneck_count": sum(
            item["status"] == "High_Utilization" for item in results
        ),
        "shortage_count": sum((item.get("shortage") or 0) > 0 for item in results),
        "bottlenecks": [
            item for item in results
            if item["status"] == "High_Utilization"
        ],
        "results": results,
        "tables": {
            normalized_type: {
                "resource_type": normalized_type,
                "title": f"{normalized_type.replace('_', ' ').title()} capacity",
                "key_figures": {
                    "demand": CAPACITY_KEY_FIGURES[normalized_type][0],
                    "usage": CAPACITY_KEY_FIGURES[normalized_type][1],
                    "supply": CAPACITY_SUPPLY_KEY_FIGURE,
                    "consumption_rate": CAPACITY_RATE_KEY_FIGURES[normalized_type],
                },
                "summary": {
                    "resource_period_count": len(table_rows),
                    "bottleneck_count": sum(item.get("status") == "High_Utilization" for item in table_rows),
                    "shortage_count": sum((item.get("shortage") or 0) > 0 for item in table_rows),
                },
                "rows": table_rows,
            }
        },
    }

def recommend_capacity_action(
    resource_type: str,
    resource: str,
    location: str,
    period: str,
    shortage: float | None = None,
    utilization_pct: float | None = None,
    headroom: float | None = None,
    contributors: list[dict] | None = None,
) -> dict:
    """Recommend a capacity response without changing SAP IBP data."""
    normalized_type = resource_type.strip().lower()
    if normalized_type == "transport":
        normalized_type = "transportation"
    if normalized_type not in CAPACITY_KEY_FIGURES:
        raise ValueError("resource_type must be handling, production, storage, or transportation")
    if not resource or not location or not period:
        raise ValueError("resource, location, and period are required")
    if shortage is not None and shortage < 0:
        raise ValueError("shortage cannot be negative")
    actions_by_type = {
        "handling": [
            "Check goods-receipt staffing and handling shifts for the affected period.",
            "Review inbound scheduling and consolidate receipts where possible.",
            "Evaluate temporary handling capacity or an alternate receiving location.",
        ],
        "storage": [
            "Review inventory placement and transfer stock to an available warehouse.",
            "Check whether excess inventory can be consumed, shipped, or rescheduled.",
            "Evaluate storage capacity expansion and its penalty or operating cost.",
        ],
        "production": [
            "Review the contributing products and reschedule production around the constrained period.",
            "Evaluate an alternate production source or approved subcontracting option.",
            "Consider capacity supply expansion and compare its cost with the shortage impact.",
        ],
        "transportation": [
            "Review the contributing lanes and move volume to an available transportation source.",
            "Reschedule shipments or consolidate loads within the affected period.",
            "Evaluate transportation capacity expansion or an alternate carrier lane.",
        ],
    }
    if shortage and shortage > 0:
        priority = "urgent_capacity_shortage"
        summary = f"{normalized_type.title()} capacity is short by {shortage:g} units in {period}."
    elif utilization_pct is not None and utilization_pct >= 80:
        priority = "high_utilization"
        summary = f"{normalized_type.title()} capacity is highly utilized at {utilization_pct:g}% in {period}."
    else:
        priority = "monitor"
        summary = f"{normalized_type.title()} capacity does not currently require an escalation in {period}."
    return {
        "resource_type": normalized_type,
        "resource": resource,
        "location": location,
        "period": period,
        "priority": priority,
        "summary": summary,
        "shortage": shortage,
        "utilization_pct": utilization_pct,
        "headroom": headroom,
        "contributors": (contributors or [])[:10],
        "actions": actions_by_type[normalized_type],
        "requires_confirmation_for_change": True,
        "data_changed": False,
    }

def update_capacity_supply(
    resource: str,
    location: str,
    period: str,
    supply: float,
    resource_type: str = "production",
    version: str | None = None,
    confirm: bool = False,
) -> dict:
    """Preview or import SUPPLY at the IBP resource-location-period level."""
    normalized_type = resource_type.strip().lower()
    if normalized_type == "transport":
        normalized_type = "transportation"
    if normalized_type not in CAPACITY_KEY_FIGURES:
        raise ValueError("resource_type must be handling, production, storage, or transportation")
    if not resource or not location:
        raise ValueError("resource and location are required")
    if normalized_type == "transportation" and location != CAPACITY_TRANSPORT_SUPPLY_LOCATION:
        raise ValueError(
            f"transportation capacity supply must use location {CAPACITY_TRANSPORT_SUPPLY_LOCATION}"
        )
    if supply < 0:
        raise ValueError("supply cannot be negative")
    period_field = f"PERIODID{IBP_PERIOD_LEVEL}_TSTAMP"
    fields = [CAPACITY_RESOURCE_FIELD, "LOCID", CAPACITY_SUPPLY_KEY_FIGURE, period_field]
    values = {
        CAPACITY_RESOURCE_FIELD: _display_id(resource),
        "LOCID": _display_id(location),
        CAPACITY_SUPPLY_KEY_FIGURE: str(supply),
        period_field: period,
    }
    result = update_planning_data(
        aggregation_fields=fields,
        aggregation_values=values,
        version=version,
        confirm=confirm,
    )
    result.update({
        "resource_type": normalized_type,
        "resource": resource,
        "location": location,
        "period": period,
        "supply": supply,
    })
    return result

# ---------------------------------------------------------------------------
# 3. Detect Anomaly in Forecast Pattern
# ---------------------------------------------------------------------------
_MOCK_STATISTICAL_FORECAST = {
    "B1": [1000, 1050, 4200, 1100, 1080, 1120],  # spike in month 3
    "B4": [800, 800, 800, 800, 800, 800],  # flatline
    "B7": [1200, 1250, 1230, 1260, 1245, 1255],  # normal
}

def detect_forecast_anomalies(
        product: str | None = None,
        location: str | None = None,
        customer: str | None = None,
        sigma_threshold: float = 3.0,
        flatline_min_periods: int = 4,
        result_scope: str = "combination",
    ) -> dict:
    """
        Scan statistical forecast time series for spikes/drops and flatlines.
        Product, location, and customer filters are optional. Product scope
        aggregates each product across locations and customers by period.
    The statistical detection itself runs in deterministic Python, not in the
    LLM prompt -- the agent only reasons over the structured result below.
    """
    if result_scope not in {"product", "combination"}:
        raise ValueError("result_scope must be 'product' or 'combination'")
    if USE_MOCK_DATA:
        series_by_key = {
            (row_product, None, None): {
                index: value for index, value in enumerate(values)
            }
            for row_product, values in _MOCK_STATISTICAL_FORECAST.items()
            if product is None or row_product == product
        }
    else:
        filters = [
            f"UOMTOID eq '{IBP_UOM_TO_ID}'",
            f"PERIODID{IBP_PERIOD_LEVEL}_REL ge 0",
        ]
        if product:
            filters.append(f"PRDID eq '{product}'")
        if location:
            filters.append(f"LOCID eq '{location}'")
        if customer:
            filters.append(f"CUSTID eq '{customer}'")
        result = _ibp_get(
            select=(
                f"PRDID,LOCID,CUSTID,PERIODID{IBP_PERIOD_LEVEL}_TSTAMP,"
                f"UOMTOID,STATISTICALFORECASTQTY"
            ),
            filter_=" and ".join(filters),
        )
        rows = result.get("d", {}).get("results", [])
        series_by_key = {}
        for row in rows:
            timestamp = row.get(f"PERIODID{IBP_PERIOD_LEVEL}_TSTAMP", "")
            key = (row["PRDID"],) if result_scope == "product" else (
                row["PRDID"], row["LOCID"], row.get("CUSTID")
            )
            series_by_key.setdefault(key, {})
            series_by_key[key][timestamp] = (
                series_by_key[key].get(timestamp, 0.0)
                + float(row["STATISTICALFORECASTQTY"])
            )
    anomalies = []
    for key, values in series_by_key.items():
        if result_scope == "product":
            product_id, location_id, customer_id = key[0], None, None
        else:
            product_id, location_id, customer_id = key
        period_ids = sorted(values)
        series = [values[period_id] for period_id in period_ids]
        series_anomalies = _find_spikes_and_drops(product_id, series, sigma_threshold)
        series_anomalies.extend(_find_flatlines(product_id, series, flatline_min_periods))
        for anomaly in series_anomalies:
            anomaly.update({"location_id": location_id, "customer_id": customer_id})
            if "period_index" in anomaly:
                anomaly["period"] = _period_month(period_ids[anomaly["period_index"]])
            elif "period_start" in anomaly:
                start = anomaly["period_start"]
                end = start + anomaly["period_count"] - 1
                anomaly["period_start"] = _period_month(period_ids[start])
                anomaly["period_end"] = _period_month(period_ids[end])
        anomalies.extend(series_anomalies)
    return {
        "forecast_type": "STATISTICALFORECASTQTY",
        "product_filter": product,
        "location_filter": location,
        "customer_filter": customer,
            "result_scope": result_scope,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
    }

def _find_spikes_and_drops(product_id: str, series: list, sigma_threshold: float) -> list:
    """
    Uses a robust z-score (median absolute deviation) rather than mean/stdev.
    A single large spike inflates ordinary stdev enough to mask itself --
    MAD is far less sensitive to the outlier it's trying to detect.
    """
    if len(series) < 3:
        return []
    deltas = [series[i] - series[i - 1] for i in range(1, len(series))]
    median_delta = statistics.median(deltas)
    abs_devs = [abs(d - median_delta) for d in deltas]
    mad = statistics.median(abs_devs)
    if mad == 0:
        delta_stddev = statistics.pstdev(deltas)
        if delta_stddev == 0:
            return []
        scores = [abs(delta - median_delta) / delta_stddev for delta in deltas]
    else:
        # 0.6745 scales MAD to be comparable to a standard deviation for normal data
        scores = [0.6745 * abs(delta - median_delta) / mad for delta in deltas]
    found = []
    for i, delta in enumerate(deltas):
        if scores[i] > sigma_threshold:
            previous_value = series[i]
            pct_change = (delta / previous_value) * 100 if previous_value else 0
            found.append(
                {
                    "product_id": product_id,
                    "type": "spike" if delta > 0 else "drop",
                    "period_index": i + 1,
                    "pct_change": round(pct_change, 0),
                }
            )
    return found

def _find_flatlines(product_id: str, series: list, min_periods: int) -> list:
    found = []
    run_value, run_len, run_start = None, 0, 0
    for i, v in enumerate(series):
        if v == run_value and v != 0:
            run_len += 1
        else:
            if run_len >= min_periods:
                found.append(
                    {
                        "product_id": product_id,
                        "type": "flatline",
                        "value": run_value,
                        "period_start": run_start,
                        "period_count": run_len,
                    }
                )
            run_value, run_len, run_start = v, 1, i
    if run_len >= min_periods:
        found.append(
            {
                "product_id": product_id,
                "type": "flatline",
                "value": run_value,
                "period_start": run_start,
                "period_count": run_len,
            }
        )
    return found

# ---------------------------------------------------------------------------
# 3. Sales History Data Readiness Check
# ---------------------------------------------------------------------------
_MOCK_LAST_LOADED_PERIOD = "2023-10"

def get_sales_history_status(
        target_period: Optional[str] = None,
        product: str | None = None,
        location: str | None = None,
        customer: str | None = None,
        result_scope: str | None = None,
    ) -> dict:
    """
        Verify actual sales quantity (ACTUALSQTY) is loaded through the target
    period. Mirrors Joule skill: getSalesHistory.
    """
    if target_period is None:
        target_period = datetime.utcnow().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", target_period):
        raise ValueError("target_period must use YYYY-MM format")
    if result_scope is None:
        result_scope = "combination" if location or customer else "product"
    if result_scope not in {"product", "location", "customer", "combination"}:
        raise ValueError(
            "result_scope must be 'product', 'location', 'customer', or 'combination'"
        )
    loaded_row_count = 0
    missing_value_count = 0
    target_period_row_count = 0
    target_period_missing_value_count = 0
    available_periods = []
    period_field = f"PERIODID{IBP_PERIOD_LEVEL}_TSTAMP"

    def has_actual_value(row: dict) -> bool:
        value = row.get("ACTUALSQTY")
        if value is None or str(value).strip() == "":
            return False
        try:
            return float(value) != 0
        except (TypeError, ValueError):
            return True

    if USE_MOCK_DATA:
        last_loaded = _MOCK_LAST_LOADED_PERIOD
        loaded_row_count = 1
        available_periods = [last_loaded]
        target_period_row_count = 1 if target_period == last_loaded else 0
    else:
        select = (
            f"PRDID,LOCID,CUSTID,PERIODID{IBP_PERIOD_LEVEL}_TSTAMP,"
            f"UOMTOID,ACTUALSQTY"
        )
        rows = []
        for product_id in _product_id_candidates(product):
            filters = [
                f"UOMTOID eq '{IBP_UOM_TO_ID}'",
                f"PERIODID{IBP_PERIOD_LEVEL}_REL ge -{int(os.environ.get('IBP_SALES_HISTORY_LOOKBACK_MONTHS', '36'))}",
                f"PERIODID{IBP_PERIOD_LEVEL}_REL le 0",
            ]
            if product_id:
                filters.append(f"PRDID eq '{product_id}'")
            if location:
                filters.append(f"LOCID eq '{_display_id(location)}'")
            if customer:
                filters.append(f"CUSTID eq '{_display_id(customer)}'")
            result = _ibp_get(select=select, filter_=" and ".join(filters))
            rows = result.get("d", {}).get("results", [])
            if rows:
                break
        rows = result.get("d", {}).get("results", [])
        period_values = {}
        period_rows = {}
        for row in rows:
            period = _period_month(row.get(period_field))
            if period is None:
                continue
            period_rows.setdefault(period, []).append(row)
            try:
                value = float(row.get("ACTUALSQTY"))
            except (TypeError, ValueError):
                value = 0.0
            period_values[period] = period_values.get(period, 0.0) + value
        if result_scope == "product":
            available_periods = sorted(
                period for period, value in period_values.items() if value != 0
            )
        else:
            available_periods = sorted(
                period
                for period, period_rows_for_period in period_rows.items()
                if any(has_actual_value(row) for row in period_rows_for_period)
            )
        last_loaded = max(available_periods) if available_periods else None
        if result_scope == "product":
            target_period_row_count = int(target_period in period_values)
            target_period_missing_value_count = int(
                period_values.get(target_period, 0.0) == 0
            )
            loaded_row_count = len(available_periods)
            missing_value_count = len(period_values) - len(available_periods)
        else:
            target_rows = period_rows.get(target_period, [])
            target_period_row_count = len(target_rows)
            target_period_missing_value_count = sum(
                1 for row in target_rows if not has_actual_value(row)
            )
            loaded_row_count = sum(1 for row in rows if has_actual_value(row))
            missing_value_count = sum(
                1 for row in rows if not has_actual_value(row)
            )
    missing_periods = []
    if available_periods:
        expected_periods = _months_between(available_periods[0], available_periods[-1])
        if USE_MOCK_DATA:
            period_rows = {last_loaded: [{}]}
        missing_periods = [
            period
            for period in expected_periods
            if period not in period_rows
            or (
                result_scope == "combination"
                and any(not has_actual_value(row) for row in period_rows[period])
            )
            or (
                result_scope in {"product", "location", "customer"}
                and not any(has_actual_value(row) for row in period_rows[period])
            )
        ]
    ready = (
        target_period_row_count > 0
        and target_period_missing_value_count == 0
    )
    target_period_missing = not ready
    target_warning = (
        f"No ACTUALSQTY data is loaded for target period {target_period}."
        if target_period_missing
        else None
    )
    warning = (
        f"Missing ACTUALSQTY data for periods: {', '.join(missing_periods)}"
        if missing_periods
        else None
    )
    return {
        "target_period": target_period,
        "product_filter": product,
        "location_filter": location,
        "customer_filter": customer,
        "result_scope": result_scope,
        "last_loaded_period": last_loaded,
        "period": last_loaded,
        "loaded_row_count": loaded_row_count,
        "missing_value_count": missing_value_count,
        "target_period_row_count": target_period_row_count,
        "target_period_missing_value_count": target_period_missing_value_count,
        "target_period_missing": target_period_missing,
        "target_warning": target_warning,
        "available_periods": available_periods,
        "missing_periods": missing_periods,
        "inconsistency_detected": bool(missing_periods),
        "warning": warning,
        "ready": ready,
    }

# ---------------------------------------------------------------------------
# 4. Communication (Email)
# ---------------------------------------------------------------------------
EMAIL_MODE = os.environ.get("EMAIL_MODE", "mock")  # mock | smtp | sendgrid

def send_email(recipient: str, subject: str, body: str) -> dict:
    """
    Send an email notification. Replaces the original CAP email service +
    BPA workflow with a direct call -- SMTP or a transactional email API.
    Mirrors Joule skill: Communication Skill.
    """
    if EMAIL_MODE == "mock":
        return {"status": "sent (mock)", "recipient": recipient, "subject": subject}
    if EMAIL_MODE == "smtp":
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = os.environ["SMTP_FROM"]
        msg["To"] = recipient
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587))) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            s.send_message(msg)
        return {"status": "sent", "recipient": recipient, "subject": subject}
    if EMAIL_MODE == "sendgrid":
        api_key = os.environ["SENDGRID_API_KEY"]
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "personalizations": [{"to": [{"email": recipient}]}],
                "from": {"email": os.environ["SENDGRID_FROM"]},
                "subject": subject,
                "content": [{"type": "text/plain", "value": body}],
            },
            timeout=15,
        )
        resp.raise_for_status()
        return {"status": "sent", "recipient": recipient, "subject": subject}
    raise ValueError(f"Unknown EMAIL_MODE: {EMAIL_MODE}")