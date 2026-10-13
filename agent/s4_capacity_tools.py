"""Read SAP S/4HANA work-center capacity and shift configuration."""
import os
from urllib.parse import quote

import requests
from dotenv import load_dotenv


load_dotenv()
S4_BASE_URL = os.environ.get("S4_BASE_URL", "").rstrip("/")
S4_USER = os.environ.get("S4_USER") or os.environ.get("S4_USERNAME", "")
S4_PASSWORD = os.environ.get("S4_PASSWORD", "")
S4_API_TIMEOUT_SECONDS = float(os.environ.get("S4_API_TIMEOUT_SECONDS", "30"))
S4_WORK_CENTERS_ROOT = "/sap/opu/odata/sap/API_WORK_CENTERS"


def _odata_literal(value: str) -> str:
    return value.replace("'", "''")


def _collection_rows(payload: dict) -> list[dict]:
    data = payload.get("d", payload)
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    if isinstance(data, dict):
        return [data]
    return []


def _s4_get(entity_path: str, params: dict | None = None) -> dict:
    if not S4_BASE_URL:
        raise RuntimeError("S4_BASE_URL is not configured")
    if not S4_USER or not S4_PASSWORD:
        raise RuntimeError("S4_USER and S4_PASSWORD must be configured")

    response = requests.get(
        f"{S4_BASE_URL}{S4_WORK_CENTERS_ROOT}/{entity_path}",
        params=params or {"$format": "json"},
        auth=(S4_USER, S4_PASSWORD),
        headers={"Accept": "application/json"},
        timeout=S4_API_TIMEOUT_SECONDS,
    )
    if not response.ok:
        raise RuntimeError(
            f"S/4HANA capacity request failed with HTTP {response.status_code} "
            f"for {entity_path}: {response.text}"
        )
    return response.json()


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

    At least one of plant or work_center is required to keep reads scoped.
    This tool is read-only and does not evaluate order load or change SAP data.
    """
    if not plant and not work_center:
        raise ValueError("provide a plant or work_center to scope the capacity read")

    filters = []
    if plant:
        filters.append(f"Plant eq '{_odata_literal(plant)}'")
    if work_center:
        filters.append(f"WorkCenter eq '{_odata_literal(work_center)}'")
    work_center_payload = _s4_get(
        "A_WorkCenters",
        {
            "$filter": " and ".join(filters),
            "$top": 200,
            "$format": "json",
        },
    )
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