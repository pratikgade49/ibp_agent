"""Read SAP S/4HANA work-center capacity and shift configuration."""
import json
import os
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


def _odata_literal(value: str) -> str:
    return value.replace("'", "''")


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
    response = requests.post(
        _oauth_token_endpoint(token_url),
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=S4_API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(
            f"Could not obtain {service_label} access token "
            f"(HTTP {response.status_code})"
        )
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError(f"{service_label} token response did not include access_token")
    return token


def _onpremise_destination_request_config() -> tuple[str, dict]:
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


def _s4_get(
    entity_path: str,
    params: dict | None = None,
    service_root: str = S4_WORK_CENTERS_ROOT,
) -> dict:
    if S4_DESTINATION_NAME:
        base_url, request_options = _onpremise_destination_request_config()
    else:
        if not S4_BASE_URL:
            raise RuntimeError("S4_BASE_URL is not configured")
        if not S4_USER or not S4_PASSWORD:
            raise RuntimeError("S4_USER and S4_PASSWORD must be configured")
        base_url = S4_BASE_URL
        request_options = {
            "auth": (S4_USER, S4_PASSWORD),
            "headers": {"Accept": "application/json"},
            "timeout": S4_API_TIMEOUT_SECONDS,
        }

    request_url = f"{base_url}{service_root}/{entity_path}"
    response = requests.get(
        request_url,
        params=params or {"$format": "json"},
        **request_options,
    )
    if not response.ok:
        raise RuntimeError(
            f"S/4HANA capacity request failed with HTTP {response.status_code} "
            f"for {entity_path}: {response.text}"
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
                f"for OData next link: {page_response.text}"
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
                "Check the SAP work-center group for configured alternate work centers, then verify their capacity before proposing a move.",
                "Do not move or dispatch the operation automatically; confirm the selected alternate and schedule with the planner.",
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