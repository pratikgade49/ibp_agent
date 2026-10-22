"""Read SAP S/4HANA work-center capacity and shift configuration."""
import json
import os
import threading
import time
from urllib.parse import quote, urljoin, urlsplit
from datetime import date, datetime, timedelta

import requests
from dotenv import load_dotenv


load_dotenv()
S4_BASE_URL = os.environ.get("S4_BASE_URL", "").rstrip("/")
S4_USER = os.environ.get("S4_USER") or os.environ.get("S4_USERNAME", "")
S4_PASSWORD = os.environ.get("S4_PASSWORD", "")
S4_DESTINATION_NAME = os.environ.get("S4_DESTINATION_NAME", "").strip()
S4_API_TIMEOUT_SECONDS = float(os.environ.get("S4_API_TIMEOUT_SECONDS", "30"))
S4_WORK_CENTERS_ROOT = "/sap/opu/odata/sap/API_WORK_CENTERS"
S4_WORK_CENTERS_UI_ROOT = "/sap/opu/odata/sap/UI_WORKCENTERS"
S4_ALTERNATE_WORK_CENTERS_ROOT = os.environ.get(
    "S4_ALTERNATE_WORK_CENTERS_ROOT",
    "/sap/opu/odata/sap/ZALT_WC_FINDER_SRV",
).rstrip("/")
S4_PRODUCTION_ORDERS_ROOT = "/sap/opu/odata/sap/API_PRODUCTION_ORDER_2_SRV"

# Production-order statuses under which SAP refuses operation changes
# (API_PRODUCTION_ORDER_2_SRV documentation).
BLOCKING_ORDER_STATUSES = {"DLT", "DLFL", "TECO", "CLSD", "LKD"}
S4_ERROR_TEXT_LIMIT = 500

# OAuth tokens and the resolved destination are cached so every OData call does
# not repeat the destination/connectivity round trips.
_CACHE_LOCK = threading.Lock()
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_DESTINATION_CACHE: dict[str, tuple[float, tuple[str, dict]]] = {}
_TOKEN_EXPIRY_MARGIN_SECONDS = 60
_DESTINATION_CACHE_SECONDS = 300


def _odata_literal(value: str) -> str:
    return value.replace("'", "''")


def _same_sap_order_id(left: object, right: str) -> bool:
    left_value = str(left or "").strip()
    right_value = str(right or "").strip()
    if left_value.isdigit() and right_value.isdigit():
        return left_value.lstrip("0") == right_value.lstrip("0")
    return left_value == right_value


def _collection_rows(payload: dict) -> list[dict]:
    data = payload.get("d", payload)
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    if isinstance(data, dict):
        return [data]
    return []


def _bound_service_credentials(service_label: str) -> dict:
    raw_bindings = os.environ.get("VCAP_SERVICES", "")
    if not raw_bindings:
        raise RuntimeError(
            f"The {service_label} service is not bound to this application"
        )
    try:
        bindings = json.loads(raw_bindings)
    except json.JSONDecodeError as exc:
        raise RuntimeError("VCAP_SERVICES is not valid JSON") from exc

    candidates = bindings.get(service_label, [])
    for service in candidates:
        if service.get("label") == service_label:
            credentials = service.get("credentials")
            if isinstance(credentials, dict):
                return credentials
    for services in bindings.values():
        for service in services:
            if service.get("label") == service_label:
                credentials = service.get("credentials")
                if isinstance(credentials, dict):
                    return credentials
    raise RuntimeError(
        f"The {service_label} service is not bound to this application"
    )


def _oauth_token_endpoint(value: str) -> str:
    endpoint = value.strip().rstrip("/")
    if not endpoint:
        raise RuntimeError("OAuth token endpoint is not configured")
    if not endpoint.endswith("/oauth/token"):
        endpoint = f"{endpoint}/oauth/token"
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("OAuth token endpoint must be an absolute HTTP(S) URL")
    return endpoint


def _service_oauth_url(credentials: dict, service_label: str) -> str:
    uaa = credentials.get("uaa")
    if not isinstance(uaa, dict):
        uaa = {}
    for candidate in (
        uaa.get("token_service_url"),
        uaa.get("url"),
        credentials.get("token_service_url"),
        credentials.get("url"),
    ):
        if candidate:
            return _oauth_token_endpoint(str(candidate))

    uaadomain = uaa.get("uaadomain") or credentials.get("uaadomain")
    if uaadomain:
        authority = str(uaadomain).strip().rstrip("/")
        if "://" not in authority:
            authority = f"https://{authority}"
        return _oauth_token_endpoint(authority)

    raise RuntimeError(
        f"{service_label} binding is missing its OAuth URL; expected "
        "credentials.uaa.url, credentials.token_service_url, or credentials.uaadomain"
    )


def _oauth_access_token(
    token_url: str,
    client_id: str,
    client_secret: str,
    service_label: str,
) -> str:
    endpoint = _oauth_token_endpoint(token_url)
    cache_key = (endpoint, client_id)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _TOKEN_CACHE.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]

    response = requests.post(
        endpoint,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=S4_API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(
            f"Could not obtain {service_label} access token "
            f"(HTTP {response.status_code})"
        )
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"{service_label} token response did not include access_token")
    try:
        ttl = float(payload.get("expires_in", 300))
    except (TypeError, ValueError):
        ttl = 300.0
    with _CACHE_LOCK:
        _TOKEN_CACHE[cache_key] = (
            token,
            now + max(ttl - _TOKEN_EXPIRY_MARGIN_SECONDS, 0.0),
        )
    return token


def _build_onpremise_destination_request_config() -> tuple[str, dict]:
    destination_credentials = _bound_service_credentials("destination")
    uaa = destination_credentials.get("uaa")
    if not isinstance(uaa, dict):
        uaa = {}
    destination_token = _oauth_access_token(
        _service_oauth_url(destination_credentials, "Destination service"),
        uaa.get("clientid", destination_credentials.get("clientid", "")),
        uaa.get("clientsecret", destination_credentials.get("clientsecret", "")),
        "Destination service",
    )
    destination_url = (
        f"{destination_credentials.get('uri', '').rstrip('/')}/destination-configuration"
        f"/v1/destinations/{quote(S4_DESTINATION_NAME, safe='')}"
    )
    destination_response = requests.get(
        destination_url,
        headers={"Authorization": f"Bearer {destination_token}"},
        timeout=S4_API_TIMEOUT_SECONDS,
    )
    if not destination_response.ok:
        raise RuntimeError(
            f"Could not resolve destination {S4_DESTINATION_NAME} "
            f"(HTTP {destination_response.status_code})"
        )
    destination = destination_response.json().get("destinationConfiguration", {})
    if destination.get("ProxyType", "").lower() != "onpremise":
        raise RuntimeError(
            f"Destination {S4_DESTINATION_NAME} must use ProxyType=OnPremise"
        )
    if destination.get("Authentication") != "BasicAuthentication":
        raise RuntimeError(
            f"Destination {S4_DESTINATION_NAME} must use BasicAuthentication"
        )
    base_url = destination.get("URL", "").rstrip("/")
    username = destination.get("User")
    password = destination.get("Password")
    if not base_url or not username or not password:
        raise RuntimeError(
            f"Destination {S4_DESTINATION_NAME} must provide URL and Basic credentials"
        )

    connectivity_credentials = _bound_service_credentials("connectivity")
    connectivity_token = _oauth_access_token(
        connectivity_credentials.get("token_service_url")
        or _service_oauth_url(connectivity_credentials, "Connectivity service"),
        connectivity_credentials.get("clientid", ""),
        connectivity_credentials.get("clientsecret", ""),
        "Connectivity service",
    )
    proxy_host = connectivity_credentials.get("onpremise_proxy_host")
    proxy_port = connectivity_credentials.get("onpremise_proxy_port")
    if not proxy_host or not proxy_port:
        raise RuntimeError(
            "Connectivity binding is missing on-premise proxy host or port"
        )
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    headers = {
        "Accept": "application/json",
        "Proxy-Authorization": f"Bearer {connectivity_token}",
    }
    location_id = destination.get("CloudConnectorLocationId")
    if location_id:
        headers["SAP-Connectivity-SCC-Location_ID"] = location_id
    return base_url, {
        "auth": (username, password),
        "headers": headers,
        "proxies": {"http": proxy_url, "https": proxy_url},
        "timeout": S4_API_TIMEOUT_SECONDS,
    }


def _onpremise_destination_request_config() -> tuple[str, dict]:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _DESTINATION_CACHE.get(S4_DESTINATION_NAME)
        if cached and cached[0] > now:
            return cached[1][0], dict(cached[1][1])
    result = _build_onpremise_destination_request_config()
    with _CACHE_LOCK:
        _DESTINATION_CACHE[S4_DESTINATION_NAME] = (
            now + _DESTINATION_CACHE_SECONDS,
            result,
        )
    return result[0], dict(result[1])


def _s4_request_config() -> tuple[str, dict]:
    if S4_DESTINATION_NAME:
        return _onpremise_destination_request_config()
    if not S4_BASE_URL:
        raise RuntimeError("S4_BASE_URL is not configured")
    if not S4_USER or not S4_PASSWORD:
        raise RuntimeError("S4_USER and S4_PASSWORD must be configured")
    return S4_BASE_URL, {
        "auth": (S4_USER, S4_PASSWORD),
        "headers": {"Accept": "application/json"},
        "timeout": S4_API_TIMEOUT_SECONDS,
    }


def _s4_get(
    entity_path: str,
    params: dict | None = None,
    service_root: str = S4_WORK_CENTERS_ROOT,
) -> dict:
    base_url, request_options = _s4_request_config()

    request_url = f"{base_url}{service_root}/{entity_path}"
    response = requests.get(
        request_url,
        params=params or {"$format": "json"},
        **request_options,
    )
    if not response.ok:
        raise RuntimeError(
            f"S/4HANA capacity request failed with HTTP {response.status_code} "
            f"for {entity_path}: {response.text[:S4_ERROR_TEXT_LIMIT]}"
        )

    payload = response.json()
    data = payload.get("d") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return payload

    rows = list(data["results"])
    next_url = data.get("__next")
    visited_urls = {request_url}
    while next_url:
        page_url = urljoin(request_url, next_url)
        if page_url in visited_urls:
            raise RuntimeError("S/4HANA capacity response repeated an OData next link")
        visited_urls.add(page_url)
        page_response = requests.get(page_url, **request_options)
        if not page_response.ok:
            raise RuntimeError(
                f"S/4HANA capacity request failed with HTTP {page_response.status_code} "
                f"for OData next link: {page_response.text[:S4_ERROR_TEXT_LIMIT]}"
            )
        page_payload = page_response.json()
        page_data = page_payload.get("d", page_payload)
        if not isinstance(page_data, dict) or not isinstance(page_data.get("results"), list):
            raise RuntimeError("S/4HANA capacity response contained an invalid OData page")
        rows.extend(page_data["results"])
        next_url = page_data.get("__next")

    data["results"] = rows
    data.pop("__next", None)
    return payload


def _clock_time(seconds: object) -> str | None:
    if seconds in (None, ""):
        return None
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _capacity_summary(row: dict) -> dict:
    return {
        "capacity_internal_id": row.get("CapacityInternalID"),
        "capacity_category_code": row.get("CapacityCategoryCode"),
        "active_version": row.get("CapacityActiveVersion"),
        "is_finite": row.get("CapacityIsFinite"),
        "is_pooled": row.get("CapacityIsPooled"),
        "has_individual_capacities": row.get("CapacityHasIndivCapacities"),
        "number_of_capacities": row.get("CapacityNumberOfCapacities"),
        "utilization_percent": row.get("CapacityPlanUtilizationPercent"),
        "break_duration_seconds": row.get("CapacityBreakDuration"),
        "factory_calendar": row.get("FactoryCalendar"),
        "start_time": _clock_time(row.get("CapacityStartTime")),
        "end_time": _clock_time(row.get("CapacityEndTime")),
        "quantity_unit": row.get("CapacityQuantityUnit"),
        "base_quantity_unit": row.get("CapacityBaseQtyUnit"),
    }


def _shift_summary(row: dict) -> dict:
    return {
        "weekday_code": row.get("WeekDay"),
        "interval_start_date": row.get("IntervalStartDate"),
        "interval_end_date": row.get("IntervalEndDate"),
        "shift_name": row.get("ShiftName"),
        "shift_start_time": row.get("ShiftStartTime"),
        "shift_end_time": row.get("ShiftEndTime"),
        "break_duration_seconds": row.get("CapacityBreakDuration"),
        "utilization_percent": row.get("CapacityPlanUtilizationPercent"),
        "number_of_capacities": row.get("CapacityNumberOfCapacities"),
        "operating_hours": row.get("TotOperatingDurationInHours"),
        "operating_duration_unit": row.get("TotOperationDurationUnit"),
    }


def read_s4_work_center_capacities(
    plant: str | None = None,
    work_center: str | None = None,
) -> dict:
    """Read work-center capacity headers and date/weekday shift records.

    Plant and work_center are optional; omitting both reads all accessible
    work centers. This tool is read-only and does not evaluate order load or
    change SAP data.
    """
    filters = []
    if plant:
        filters.append(f"Plant eq '{_odata_literal(plant)}'")
    if work_center:
        filters.append(f"WorkCenter eq '{_odata_literal(work_center)}'")
    query_params = {"$top": 200, "$format": "json"}
    if filters:
        query_params["$filter"] = " and ".join(filters)
    work_center_payload = _s4_get("A_WorkCenters", query_params)
    work_center_rows = _collection_rows(work_center_payload)
    collection_data = work_center_payload.get("d", work_center_payload)
    work_center_list_truncated = bool(
        isinstance(collection_data, dict) and collection_data.get("__next")
    )

    results = []
    for work_center_row in work_center_rows:
        capacity_id = work_center_row.get("CapacityInternalID")
        capacity = None
        shifts = []
        if capacity_id:
            escaped_capacity_id = quote(str(capacity_id), safe="")
            capacity_payload = _s4_get(
                f"A_WorkCenterCapacity('{escaped_capacity_id}')",
                {"$format": "json"},
            )
            capacity_data = capacity_payload.get("d", capacity_payload)
            capacity = _capacity_summary(capacity_data)
            active_version = capacity_data.get("CapacityActiveVersion")
            shift_filters = [
                "WorkCenterInternalID eq "
                f"'{_odata_literal(str(work_center_row.get('WorkCenterInternalID', '')))}'",
                "WorkCenterTypeCode eq "
                f"'{_odata_literal(str(work_center_row.get('WorkCenterTypeCode', '')))}'",
                f"CapacityInternalID eq '{_odata_literal(str(capacity_id))}'",
            ]
            if active_version not in (None, ""):
                shift_filters.append(
                    "CapacityActiveVersion eq "
                    f"'{_odata_literal(str(active_version))}'"
                )
            shift_payload = _s4_get(
                "A_WorkCenterCapacityShift",
                {
                    "$filter": " and ".join(shift_filters),
                    "$format": "json",
                },
            )
            shifts = [
                _shift_summary(row) for row in _collection_rows(shift_payload)
            ]

        results.append(
            {
                "work_center": work_center_row.get("WorkCenter"),
                "description": work_center_row.get("WorkCenterDesc"),
                "plant": work_center_row.get("Plant"),
                "work_center_type_code": work_center_row.get("WorkCenterTypeCode"),
                "work_center_internal_id": work_center_row.get("WorkCenterInternalID"),
                "work_center_category_code": work_center_row.get("WorkCenterCategoryCode"),
                "capacity_internal_id": capacity_id,
                "validity_start_date": work_center_row.get("ValidityStartDate"),
                "validity_end_date": work_center_row.get("ValidityEndDate"),
                "capacity": capacity,
                "shifts": shifts,
            }
        )

    return {
        "status": "completed",
        "read_only": True,
        "filters": {"plant": plant, "work_center": work_center},
        "work_center_count": len(results),
        "work_center_list_truncated": work_center_list_truncated,
        "work_centers": results,
    }


def _evaluation_parameter_path(
    entity_set: str,
    start_date: str,
    end_date: str,
    bucket_type: str,
) -> str:
    for label, value in (("start_date", start_date), ("end_date", end_date)):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must use YYYY-MM-DD format") from exc
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    if not bucket_type or len(bucket_type) != 1:
        raise ValueError("bucket_type must be a one-character SAP bucket code")

    return (
        f"{entity_set}(P_CapEvalStartDate=datetime'{start_date}T00:00:00',"
        f"P_CapEvalEndDate=datetime'{end_date}T00:00:00',"
        f"P_CapEvalBucketType='{_odata_literal(bucket_type)}')/Set"
    )


def _float_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _sap_error_summary(response) -> str:
    """Return SAP's error code, message and every errordetails entry."""
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        return response.text[:S4_ERROR_TEXT_LIMIT]
    if not isinstance(error, dict) or not error:
        return response.text[:S4_ERROR_TEXT_LIMIT]
    message = error.get("message", "")
    if isinstance(message, dict):
        message = message.get("value", "")
    code = error.get("code")
    parts = [f"{code}: {message}" if code else str(message)]
    inner = error.get("innererror", {})
    details = inner.get("errordetails", []) if isinstance(inner, dict) else []
    seen = {parts[0]}
    for detail in details or []:
        if not isinstance(detail, dict) or not detail.get("message"):
            continue
        text = (
            f"{detail['code']}: {detail['message']}"
            if detail.get("code")
            else str(detail["message"])
        )
        if text not in seen:
            seen.add(text)
            parts.append(text)
    return " | ".join(parts)[: S4_ERROR_TEXT_LIMIT * 2]


def _same_operation(left: object, right: object) -> bool:
    left_value = str(left or "").strip()
    right_value = str(right or "").strip()
    if left_value.isdigit() and right_value.isdigit():
        return left_value.lstrip("0") == right_value.lstrip("0")
    return left_value == right_value


def _operation_required_capacity(
    plant: str,
    work_center: str,
    order_id: str,
    operation: str,
    evaluation_date: str,
) -> tuple[float | None, str | None]:
    """Find the SAP-evaluated capacity this operation consumes on the source work center.

    Uses the contributor rows of SAP's own evaluation. If several rows match
    (one per capacity category), the largest is used, which is conservative.
    Returns (None, None) when the operation cannot be found.
    """
    try:
        result = analyze_s4_capacity_load(
            plant=plant,
            work_center=work_center,
            start_date=evaluation_date,
            end_date=evaluation_date,
            bucket_type="D",
        )
    except Exception:
        return None, None
    required_values = []
    unit = None
    for row in result.get("contributors", []):
        if (
            _same_sap_order_id(row.get("order_id"), order_id)
            and _same_operation(row.get("operation"), operation)
            and row.get("required_capacity") is not None
        ):
            required_values.append(row["required_capacity"])
            unit = row.get("capacity_unit") or unit
    if not required_values:
        return None, None
    return max(required_values), unit


def _capacity_fit(
    capacity_rows: list[dict],
    required: float | None,
    required_unit: str | None,
) -> dict:
    """Check whether SAP-evaluated remaining capacity can absorb the operation.

    sufficient_for_operation is None (unverified) when the required capacity is
    unknown, no buckets were returned, or units differ.
    """
    fit = {
        "required_capacity_for_operation": required,
        "required_capacity_unit": required_unit,
        "sufficient_for_operation": None,
        "remaining_after_move": None,
    }
    if required is None or not capacity_rows:
        return fit
    remaining_values = []
    for row in capacity_rows:
        remaining = row.get("remaining_capacity")
        row_unit = row.get("capacity_unit")
        if remaining is None or (required_unit and row_unit and required_unit != row_unit):
            return fit
        remaining_values.append(remaining)
    after = min(remaining_values) - required
    fit["remaining_after_move"] = round(after, 3)
    fit["sufficient_for_operation"] = after >= 0
    return fit


def _blocking_order_statuses(order_id: str) -> list[str]:
    rows = _collection_rows(
        _s4_get(
            "A_ProductionOrderStatus_2",
            {
                "$filter": f"ManufacturingOrder eq '{_odata_literal(order_id)}'",
                "$format": "json",
            },
            service_root=S4_PRODUCTION_ORDERS_ROOT,
        )
    )
    active = {str(row.get("StatusShortName", "")).strip().upper() for row in rows}
    return sorted(active & BLOCKING_ORDER_STATUSES)


def analyze_s4_capacity_load(
    plant: str | None = None,
    work_center: str | None = None,
    start_date: str = "",
    end_date: str = "",
    bucket_type: str = "D",
) -> dict:
    """Read evaluated capacity for a period, optionally scoped to plant/work center."""
    date_range_defaulted = not start_date or not end_date
    if not start_date:
        start_date = date.today().isoformat()
    if not end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date = (start + timedelta(days=29)).isoformat()

    filters = []
    if plant:
        filters.append(f"Plant eq '{_odata_literal(plant)}'")
    if work_center:
        filters.append(f"WorkCenter eq '{_odata_literal(work_center)}'")
    query_params = {"$format": "json"}
    if filters:
        query_params["$filter"] = " and ".join(filters)
    evaluation_path = _evaluation_parameter_path(
        "A_WorkCenterCapPerBucket", start_date, end_date, bucket_type
    )
    contributor_path = _evaluation_parameter_path(
        "A_WorkCenterCapOrderPerBucket", start_date, end_date, bucket_type
    )
    aggregate_rows = _collection_rows(
        _s4_get(
            evaluation_path,
            query_params,
        )
    )
    contributor_rows = _collection_rows(
        _s4_get(
            contributor_path,
            query_params,
        )
    )

    evaluations = []
    for row in aggregate_rows:
        required = _float_or_none(row.get("WorkCenterCapRqmtInCapUnit"))
        available = _float_or_none(row.get("WorkCenterAvailableCapacity"))
        remaining = _float_or_none(row.get("WrkCtrRmngCapInCapUnit"))
        utilization = _float_or_none(row.get("WorkCenterTotUtilznInTmePerd"))
        if utilization is not None and utilization <= 10:
            utilization *= 100
        if utilization is None and required is not None and available:
            utilization = required / available * 100

        evaluation = {
                "date": row.get("CapacityEvaluationTimePeriod"),
                "date_text": row.get("CapacityEvaluationTimePerdText"),
                "plant": row.get("Plant"),
                "work_center": row.get("WorkCenter"),
                "capacity_internal_id": row.get("CapacityInternalID"),
                "capacity_category_allocation": row.get("CapacityCategoryAllocation"),
                "shift_name": row.get("ShiftName"),
                "capacity_start_time_seconds": row.get("CapacityStartTime"),
                "capacity_end_time_seconds": row.get("CapacityEndTime"),
                "required_capacity": required,
                "available_capacity": available,
                "remaining_capacity": remaining,
                "utilization_percent": round(utilization, 1) if utilization is not None else None,
                "capacity_unit": row.get("WorkCenterCapacityUnit"),
                "status": (
                    "overloaded"
                    if remaining is not None and remaining < 0
                    else "within_capacity"
                    if remaining is not None
                    else "capacity_data_incomplete"
                ),
            }
        if evaluation["status"] == "overloaded":
            evaluation["recommended_actions"] = [
                "I recommend checking for an alternate work center and confirming it has enough available capacity before proposing a move.",
                "Please confirm the selected work center and schedule with the planner before making any changes.",
            ]
        evaluations.append(evaluation)

    contributors = [
        {
            "date": row.get("CapacityEvaluationTimePeriod"),
            "plant": row.get("Plant"),
            "work_center": row.get("WorkCenter"),
            "order_id": row.get("OrderID"),
            "operation": row.get("Operation"),
            "material": row.get("Material"),
            "order_status": row.get("OrderStatusText"),
            "operation_status": row.get("OperationPlanningStatusText"),
            "required_capacity": _float_or_none(row.get("WorkCenterCapRqmtInCapUnit")),
            "available_capacity": _float_or_none(row.get("WorkCenterAvailableCapacity")),
            "capacity_unit": row.get("WorkCenterCapacityUnit"),
        }
        for row in contributor_rows
    ]

    recommendations = [
        {
            "date": evaluation.get("date"),
            "plant": evaluation.get("plant"),
            "work_center": evaluation.get("work_center"),
            "remaining_capacity": evaluation.get("remaining_capacity"),
            "recommended_actions": evaluation.get("recommended_actions", []),
        }
        for evaluation in evaluations
        if evaluation.get("status") == "overloaded"
    ]

    return {
        "status": "completed",
        "read_only": True,
        "source": "SAP evaluated capacity API",
        "plant": plant,
        "work_center": work_center,
        "evaluation": {
            "start_date": start_date,
            "end_date": end_date,
            "bucket_type": bucket_type,
            "date_range_defaulted": date_range_defaulted,
        },
        "capacity_evaluations": evaluations,
        "evaluated_bucket_dates": sorted(
            {str(row["date"]) for row in evaluations if row.get("date")}
        ),
        "contributors": contributors,
        "evaluation_count": len(evaluations),
        "contributor_count": len(contributors),
        "recommendations": recommendations,
    }


def recommend_s4_group_alternates(
    plant: str,
    work_center: str,
    evaluation_date: str,
) -> dict:
    """Read alternate work centers from configured work-center groups and check capacity."""
    if not plant or not work_center:
        raise ValueError("plant and work_center are required")
    try:
        datetime.strptime(evaluation_date, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError("evaluation_date must use YYYY-MM-DD format") from exc

    source_rows = _collection_rows(
        _s4_get(
            "A_WorkCenters",
            {
                "$filter": (
                    f"Plant eq '{_odata_literal(plant)}' and "
                    f"WorkCenter eq '{_odata_literal(work_center)}'"
                ),
                "$format": "json",
            },
        )
    )
    if not source_rows:
        return {
            "status": "not_found",
            "read_only": True,
            "plant": plant,
            "work_center": work_center,
            "evaluation_date": evaluation_date,
            "alternate_count": 0,
            "alternates": [],
            "message": "The requested work center was not found in SAP work-center master data.",
        }

    source = source_rows[0]
    source_internal_id = source.get("WorkCenterInternalID")
    source_type = source.get("WorkCenterTypeCode")
    if not source_internal_id or not source_type:
        raise RuntimeError("SAP work-center master data is missing the internal ID or type code")

    group_rows = _collection_rows(
        _s4_get(
            "WorkCenterGroupWhereUsed",
            {
                "$filter": (
                    f"Plant eq '{_odata_literal(plant)}' and "
                    f"WorkCenterInternalID eq '{_odata_literal(str(source_internal_id))}' and "
                    f"WorkCenterTypeCode eq '{_odata_literal(str(source_type))}'"
                ),
                "$format": "json",
            },
            service_root=S4_WORK_CENTERS_UI_ROOT,
        )
    )

    all_plant_centers = _collection_rows(
        _s4_get(
            "A_WorkCenters",
            {
                "$filter": f"Plant eq '{_odata_literal(plant)}'",
                "$format": "json",
            },
        )
    )
    centers_by_identity = {
        (str(row.get("WorkCenterInternalID")), str(row.get("WorkCenterTypeCode"))): row
        for row in all_plant_centers
        if row.get("WorkCenterInternalID") and row.get("WorkCenterTypeCode")
    }

    candidate_identities = set()
    group_summaries = []
    for group in group_rows:
        group_id = group.get("WorkCenterGroupInternalID")
        group_type = group.get("WorkCenterGroupTypeCode")
        if not group_id or not group_type:
            continue
        group_summaries.append(
            {
                "group_id": group_id,
                "group_type_code": group_type,
                "group": group.get("WorkCenterGroup"),
                "description": group.get("WorkCenterGroupDescription"),
            }
        )
        tree_rows = _collection_rows(
            _s4_get(
                "WorkCenterGroupTreeAggrgn",
                {
                    "$filter": (
                        f"WorkCenterGroupInternalID eq '{_odata_literal(str(group_id))}'"
                    ),
                    "$format": "json",
                },
                service_root=S4_WORK_CENTERS_UI_ROOT,
            )
        )
        for tree_row in tree_rows:
            member_id = tree_row.get("WorkCenterInternalID")
            member_type = tree_row.get("WorkCenterTypeCode") or source_type
            if member_id:
                candidate_identities.add((str(member_id), str(member_type)))

    candidate_identities.discard((str(source_internal_id), str(source_type)))
    alternates = []
    for identity in sorted(candidate_identities):
        member = centers_by_identity.get(identity)
        if member is None:
            continue
        alternate_name = member.get("WorkCenter")
        if not alternate_name:
            continue
        capacity_result = analyze_s4_capacity_load(
            plant=plant,
            work_center=alternate_name,
            start_date=evaluation_date,
            end_date=evaluation_date,
            bucket_type="D",
        )
        capacity_rows = capacity_result["capacity_evaluations"]
        if not capacity_rows:
            capacity_status = "capacity_not_evaluated"
        elif any(row["status"] == "overloaded" for row in capacity_rows):
            capacity_status = "overloaded"
        elif all(
            row.get("remaining_capacity") is not None
            and row["remaining_capacity"] >= 0
            for row in capacity_rows
        ):
            capacity_status = "within_capacity"
        else:
            capacity_status = "capacity_data_incomplete"
        alternates.append(
            {
                "work_center": alternate_name,
                "description": member.get("WorkCenterDesc"),
                "work_center_internal_id": member.get("WorkCenterInternalID"),
                "work_center_type_code": member.get("WorkCenterTypeCode"),
                "capacity_status": capacity_status,
                "capacity_evaluations": capacity_rows,
            }
        )

    return {
        "status": "completed",
        "read_only": True,
        "plant": plant,
        "work_center": work_center,
        "evaluation_date": evaluation_date,
        "alternate_source": "SAP work-center group membership",
        "groups": group_summaries,
        "alternate_count": len(alternates),
        "alternates": alternates,
        "message": (
            None
            if alternates
            else "No alternate work centers are assigned through a work-center group in SAP."
        ),
    }


def recommend_s4_routing_alternates(
    plant: str,
    work_center: str,
    order_id: str,
    operation: str,
    material: str,
    evaluation_date: str,
) -> dict:
    """Read Class 019 operation alternates and check their SAP capacity."""
    if not all((plant, work_center, order_id, operation, material)):
        raise ValueError(
            "plant, work_center, order_id, operation, and material are required"
        )
    try:
        datetime.strptime(evaluation_date, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError("evaluation_date must use YYYY-MM-DD format") from exc

    query = {
        "$filter": " and ".join(
            (
                f"SrcWerks eq '{_odata_literal(plant)}'",
                f"SrcArbpl eq '{_odata_literal(work_center)}'",
            )
        ),
        "$format": "json",
    }
    rows = _collection_rows(
        _s4_get(
            "AlternateWCSet",
            query,
            service_root=S4_ALTERNATE_WORK_CENTERS_ROOT,
        )
    )

    alternates = []
    seen_centers = set()
    match_rows = [
        row
        for row in rows
        if _same_sap_order_id(row.get("Aufnr"), order_id)
        and str(row.get("Vornr", "")).strip() == operation
        and str(row.get("Matnr", "")).strip() == material
        and str(row.get("MatchStatus", "")).strip().upper() == "MATCH"
    ]
    required_capacity, required_unit = (
        _operation_required_capacity(plant, work_center, order_id, operation, evaluation_date)
        if match_rows
        else (None, None)
    )
    for row in match_rows:
        alternate_plant = row.get("AltWerks")
        alternate_work_center = row.get("AltArbpl")
        identity = (alternate_plant, alternate_work_center)
        if not alternate_plant or not alternate_work_center or identity in seen_centers:
            continue
        seen_centers.add(identity)

        capacity_result = analyze_s4_capacity_load(
            plant=alternate_plant,
            work_center=alternate_work_center,
            start_date=evaluation_date,
            end_date=evaluation_date,
            bucket_type="D",
        )
        capacity_rows = capacity_result["capacity_evaluations"]
        if not capacity_rows:
            capacity_status = "capacity_not_evaluated"
        elif any(item["status"] == "overloaded" for item in capacity_rows):
            capacity_status = "overloaded"
        elif all(
            item.get("remaining_capacity") is not None
            and item["remaining_capacity"] >= 0
            for item in capacity_rows
        ):
            capacity_status = "within_capacity"
        else:
            capacity_status = "capacity_data_incomplete"

        alternates.append(
            {
                "work_center": alternate_work_center,
                "plant": alternate_plant,
                "capacity_status": capacity_status,
                "capacity_evaluations": capacity_rows,
                **_capacity_fit(capacity_rows, required_capacity, required_unit),
                "match_status": row.get("MatchStatus"),
                "source_class": row.get("SrcClass"),
                "source_characteristic": row.get("SrcAtnam"),
                "source_characteristic_value": row.get("SrcAtwrt"),
                "alternate_characteristic_value": row.get("AltAtwrt"),
                "message": row.get("Message"),
                "order_id": row.get("Aufnr"),
                "operation": row.get("Vornr"),
                "material": row.get("Matnr"),
                "routing_type": row.get("Plnty"),
                "routing_group": row.get("Plnnr"),
                "routing_counter": row.get("Plnal"),
            }
        )

    return {
        "status": "completed",
        "read_only": True,
        "plant": plant,
        "work_center": work_center,
        "order_id": order_id,
        "operation": operation,
        "material": material,
        "evaluation_date": evaluation_date,
        "operation_required_capacity": required_capacity,
        "operation_required_capacity_unit": required_unit,
        "alternate_source": "ZALT_WC_FINDER_SRV Class 019 operation classification",
        "alternate_count": len(alternates),
        "alternates": alternates,
        "message": (
            None
            if alternates
            else "The alternate-work-center finder returned no MATCH rows for this routing operation."
        ),
    }


def update_s4_production_order_operation_work_center(
    order_id: str,
    operation: str,
    target_work_center: str,
    evaluation_date: str,
    approval_phrase: str = "",
    sequence: str | None = None,
) -> dict:
    """Prepare or explicitly approve a Class 019-validated operation reassignment."""
    if not all((order_id, operation, target_work_center)):
        raise ValueError("order_id, operation, and target_work_center are required")
    try:
        datetime.strptime(evaluation_date, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError("evaluation_date must use YYYY-MM-DD format") from exc

    order_key = _odata_literal(order_id)
    order_payload = _s4_get(
        f"A_ProductionOrder_2('{order_key}')",
        {"$expand": "to_ProductionOrderOperation", "$format": "json"},
        service_root=S4_PRODUCTION_ORDERS_ROOT,
    )
    order = order_payload.get("d", order_payload)
    operation_rows = _collection_rows(order.get("to_ProductionOrderOperation", {}))
    matching_operations = [
        row
        for row in operation_rows
        if str(row.get("ManufacturingOrderOperation", "")).strip() == operation
        and (
            sequence is None
            or str(row.get("ManufacturingOrderSequence", "")).strip() == sequence
        )
    ]
    if not matching_operations:
        return {
            "status": "operation_not_found",
            "read_only": True,
            "order_id": order_id,
            "operation": operation,
            "message": "SAP returned no matching production-order operation.",
        }
    if len(matching_operations) > 1:
        return {
            "status": "operation_ambiguous",
            "read_only": True,
            "order_id": order_id,
            "operation": operation,
            "message": "Multiple matching operation sequences exist; provide sequence to identify one.",
        }

    current = matching_operations[0]
    current_work_center = str(current.get("WorkCenter", "")).strip()
    plant = str(current.get("ProductionPlant", "")).strip()
    material = str(order.get("Material", "")).strip()
    if not plant or not current_work_center or not material:
        raise RuntimeError(
            "SAP operation/order data must include ProductionPlant, WorkCenter, and Material"
        )
    if current_work_center == target_work_center:
        return {
            "status": "already_assigned",
            "read_only": True,
            "order_id": order_id,
            "operation": operation,
            "work_center": target_work_center,
            "message": "The operation is already assigned to the requested work center.",
        }

    try:
        blocking_statuses = _blocking_order_statuses(
            str(order.get("ManufacturingOrder") or order_id)
        )
    except Exception:
        blocking_statuses = []  # SAP will still reject a blocked order on PATCH
    if blocking_statuses:
        return {
            "status": "order_status_blocks_update",
            "read_only": True,
            "order_id": order_id,
            "operation": operation,
            "blocking_statuses": blocking_statuses,
            "message": "The production order is deleted, technically completed, closed, or locked; reassignment was not attempted.",
        }

    finder_rows = _collection_rows(
        _s4_get(
            "AlternateWCSet",
            {
                "$filter": (
                    f"SrcWerks eq '{_odata_literal(plant)}' and "
                    f"SrcArbpl eq '{_odata_literal(current_work_center)}'"
                ),
                "$format": "json",
            },
            service_root=S4_ALTERNATE_WORK_CENTERS_ROOT,
        )
    )
    matching_alternates = [
        row
        for row in finder_rows
        if _same_sap_order_id(row.get("Aufnr"), order_id)
        and str(row.get("Vornr", "")).strip() == operation
        and str(row.get("Matnr", "")).strip() == material
        and str(row.get("AltWerks", "")).strip() == plant
        and str(row.get("AltArbpl", "")).strip() == target_work_center
        and str(row.get("MatchStatus", "")).strip().upper() == "MATCH"
    ]
    if not matching_alternates:
        return {
            "status": "target_not_classified_alternate",
            "read_only": True,
            "order_id": order_id,
            "operation": operation,
            "plant": plant,
            "current_work_center": current_work_center,
            "target_work_center": target_work_center,
            "message": "The Class 019 finder did not return this target as a MATCH for the operation.",
        }

    capacity_result = analyze_s4_capacity_load(
        plant=plant,
        work_center=target_work_center,
        start_date=evaluation_date,
        end_date=evaluation_date,
        bucket_type="D",
    )
    capacity_rows = capacity_result["capacity_evaluations"]
    if not capacity_rows or not all(
        row.get("remaining_capacity") is not None
        and row["remaining_capacity"] >= 0
        for row in capacity_rows
    ):
        return {
            "status": "target_capacity_not_confirmed",
            "read_only": True,
            "order_id": order_id,
            "operation": operation,
            "plant": plant,
            "current_work_center": current_work_center,
            "target_work_center": target_work_center,
            "evaluation_date": evaluation_date,
            "capacity_evaluations": capacity_rows,
            "message": "SAP did not return only nonnegative remaining-capacity buckets for the target work center/date.",
        }

    required_capacity, required_unit = _operation_required_capacity(
        plant, current_work_center, order_id, operation, evaluation_date
    )
    capacity_fit = _capacity_fit(capacity_rows, required_capacity, required_unit)
    if capacity_fit["sufficient_for_operation"] is False:
        return {
            "status": "target_capacity_insufficient_for_operation",
            "read_only": True,
            "order_id": order_id,
            "operation": operation,
            "plant": plant,
            "current_work_center": current_work_center,
            "target_work_center": target_work_center,
            "evaluation_date": evaluation_date,
            "capacity_evaluations": capacity_rows,
            **capacity_fit,
            "message": "The target work center's SAP-evaluated remaining capacity is smaller than the capacity this operation requires.",
        }

    target_rows = _collection_rows(
        _s4_get(
            "A_WorkCenters",
            {
                "$filter": (
                    f"Plant eq '{_odata_literal(plant)}' and "
                    f"WorkCenter eq '{_odata_literal(target_work_center)}'"
                ),
                "$format": "json",
            },
        )
    )
    if len(target_rows) != 1:
        return {
            "status": "target_work_center_not_unique",
            "read_only": True,
            "order_id": order_id,
            "operation": operation,
            "plant": plant,
            "target_work_center": target_work_center,
            "target_count": len(target_rows),
            "message": "SAP work-center master did not resolve the target to exactly one work-center identity.",
        }
    target_center = target_rows[0]
    target_internal_id = str(target_center.get("WorkCenterInternalID", "")).strip()
    target_type_code = str(target_center.get("WorkCenterTypeCode", "")).strip()
    if not target_internal_id or not target_type_code:
        raise RuntimeError("SAP target work-center master is missing its internal ID or type code")

    blocked_flags = (
        "OperationIsConfirmed",
        "OperationIsTechlyCompleted",
        "OperationIsClosed",
        "OperationIsDeleted",
    )
    if any(str(current.get(flag, "")).strip().upper() in {"X", "TRUE"} for flag in blocked_flags):
        return {
            "status": "operation_status_blocks_update",
            "read_only": True,
            "order_id": order_id,
            "operation": operation,
            "current_work_center": current_work_center,
            "target_work_center": target_work_center,
            "message": "The operation is confirmed, completed, closed, or deleted; reassignment was not attempted.",
        }

    expected_approval = f"APPROVE {order_id} {operation} {target_work_center} ON {evaluation_date}"
    if approval_phrase.strip() != expected_approval:
        return {
            "status": "approval_required",
            "read_only": True,
            "order_id": order_id,
            "operation": operation,
            "plant": plant,
            "material": material,
            "current_work_center": current_work_center,
            "target_work_center": target_work_center,
            "target_work_center_internal_id": target_internal_id,
            "target_work_center_type_code": target_type_code,
            "evaluation_date": evaluation_date,
            "classification_match": matching_alternates[0],
            "capacity_evaluations": capacity_rows,
            **capacity_fit,
            "approval_phrase": expected_approval,
            "message": "No SAP change was made. Explicit approval of this exact reassignment is required.",
        }

    internal_order_id = str(current.get("OrderInternalBillOfOperations", "")).strip()
    internal_operation_id = str(current.get("OrderIntBillOfOperationsItem", "")).strip()
    if not internal_order_id or not internal_operation_id:
        raise RuntimeError("SAP operation response is missing its internal entity keys")
    operation_entity = (
        "A_ProductionOrderOperation_2("
        f"OrderInternalBillOfOperations='{_odata_literal(internal_order_id)}',"
        f"OrderIntBillOfOperationsItem='{_odata_literal(internal_operation_id)}')"
    )
    base_url, request_options = _s4_request_config()
    operation_url = f"{base_url}{S4_PRODUCTION_ORDERS_ROOT}/{operation_entity}"
    csrf_options = dict(request_options)
    csrf_options["headers"] = {
        **request_options.get("headers", {}),
        "x-csrf-token": "Fetch",
    }
    csrf_response = requests.get(
        operation_url,
        params={"$format": "json"},
        **csrf_options,
    )
    if not csrf_response.ok:
        raise RuntimeError(
            f"Could not fetch SAP CSRF token (HTTP {csrf_response.status_code})"
        )
    csrf_token = csrf_response.headers.get("x-csrf-token")
    csrf_payload = csrf_response.json()
    csrf_operation = csrf_payload.get("d", csrf_payload)
    etag = csrf_response.headers.get("ETag") or csrf_operation.get(
        "__metadata", {}
    ).get("etag")
    if not etag and csrf_operation.get("LastChangeDateTime"):
        etag = f'W/"\'{csrf_operation["LastChangeDateTime"]}\'"'
    if not csrf_token or not etag:
        raise RuntimeError("SAP response must include both CSRF token and ETag")

    patch_options = dict(request_options)
    patch_options["headers"] = {
        **request_options.get("headers", {}),
        "x-csrf-token": csrf_token,
        "If-Match": etag,
        "Content-Type": "application/json",
    }
    patch_options["cookies"] = csrf_response.cookies
    # SAP resolves AFVGD-ARBPL in the operation's plant, so the plant is sent
    # together with the work center (as in the API documentation examples).
    patch_payload = {"WorkCenter": target_work_center, "ProductionPlant": plant}
    control_profile = str(current.get("OperationControlProfile", "")).strip()
    if control_profile:
        patch_payload["OperationControlProfile"] = control_profile
    patch_response = requests.patch(
        operation_url,
        json=patch_payload,
        **patch_options,
    )
    if not patch_response.ok:
        print("S4 PATCH FAILED", operation_url, patch_payload,
            patch_response.text[:4000], flush=True)  # shows in cf logs
        raise RuntimeError(
            f"SAP production-order operation update failed with HTTP "
            f"{patch_response.status_code}: {_sap_error_summary(patch_response)} "
            f"| payload={patch_payload}"
        )

    verify_payload = _s4_get(
        operation_entity,
        {"$format": "json"},
        service_root=S4_PRODUCTION_ORDERS_ROOT,
    )
    verified_operation = verify_payload.get("d", verify_payload)
    verified_work_center = str(verified_operation.get("WorkCenter", "")).strip()
    if verified_work_center != target_work_center:
        raise RuntimeError(
            "SAP accepted the update, but the follow-up read did not confirm the target work center"
        )
    verified_internal_id = str(verified_operation.get("WorkCenterInternalID", "")).strip()
    if verified_internal_id and verified_internal_id.lstrip("0") != target_internal_id.lstrip("0"):
        raise RuntimeError(
            "SAP accepted the update, but the follow-up read shows a different work-center internal ID"
        )

    return {
        "status": "updated",
        "read_only": False,
        "order_id": order_id,
        "operation": operation,
        "plant": plant,
        "material": material,
        "previous_work_center": current_work_center,
        "work_center": verified_work_center,
        "evaluation_date": evaluation_date,
        "capacity_evaluations": capacity_rows,
        **capacity_fit,
        "patched_fields": sorted(patch_payload),
        "message": "SAP confirmed the production-order operation work-center update.",
    }
