"""Read SAP S/4HANA maintenance calls and their production work centers."""
from datetime import date, datetime, timedelta
import os

from agent.s4_capacity_tools import _collection_rows, _odata_literal, _s4_get


S4_MAINTENANCE_SERVICE_ROOT = os.environ.get(
    "S4_MAINTENANCE_SERVICE_ROOT",
    "/sap/opu/odata/sap/UI_MAINTENANCE_PLAN",
).rstrip("/")


def _date_value(value: object) -> str | None:
    if not value:
        return None
    if isinstance(value, str) and value.startswith("/Date("):
        try:
            milliseconds = int(value[6:].split(")", 1)[0])
            return datetime.utcfromtimestamp(milliseconds / 1000).date().isoformat()
        except (ValueError, OverflowError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value[:10]).date().isoformat()
        except ValueError:
            return None
    return None


def _time_value(value: object) -> str | None:
    if not value:
        return None
    if isinstance(value, str) and value.startswith("/Date("):
        try:
            milliseconds = int(value[6:].split(")", 1)[0])
            return datetime.utcfromtimestamp(milliseconds / 1000).time().isoformat()
        except (ValueError, OverflowError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).time().isoformat()
        except ValueError:
            return None
    return None


def _date_filter_literal(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError("Dates must use YYYY-MM-DD format") from exc
    return f"datetime'{parsed.date().isoformat()}T00:00:00'"


def read_s4_maintenance_schedule(
    start_date: str = "",
    end_date: str = "",
    plant: str | None = None,
    work_center: str | None = None,
) -> dict:
    """Read scheduled maintenance calls and join them to production work centers."""
    if not start_date:
        start_date = date.today().isoformat()
    if not end_date:
        end_date = (datetime.strptime(start_date, "%Y-%m-%d").date() + timedelta(days=29)).isoformat()
    start_literal = _date_filter_literal(start_date)
    end_literal = _date_filter_literal(end_date)
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    calls = _collection_rows(
        _s4_get(
            "C_MaintenancePlanScheduleTP",
            {
                "$filter": (
                    f"MaintenanceCallNextPlannedDate ge {start_literal} and "
                    f"MaintenanceCallNextPlannedDate le {end_literal}"
                ),
                "$select": ",".join((
                    "MaintenancePlan", "MaintenancePlanCallNumber", "MaintenancePlanDesc",
                    "MaintenanceCallNextPlannedDate", "MaintenanceCallDate",
                    "SchedulingStatus", "SchedulingStatusText", "SchedulingTypeName",
                    "MaintenanceCallIsOutstanding", "CompletionDate",
                    "MaintenancePlanCallObject",
                )),
                "$format": "json",
            },
            service_root=S4_MAINTENANCE_SERVICE_ROOT,
        )
    )

    maintenance_items: dict[str, dict] = {}
    schedule_rows = []
    for call in calls:
        plan = str(call.get("MaintenancePlan") or "").strip()
        call_number = call.get("MaintenancePlanCallNumber")
        if not plan or call_number is None:
            continue
        call_objects = _collection_rows(
            _s4_get(
                "C_MaintPlanCallObject",
                {
                    "$filter": (
                        f"MaintenancePlan eq '{_odata_literal(plan)}' and "
                        f"MaintenancePlanCallNumber eq {int(call_number)}"
                    ),
                    "$format": "json",
                },
                service_root=S4_MAINTENANCE_SERVICE_ROOT,
            )
        )
        for call_object in call_objects:
            item_id = str(call_object.get("MaintenanceItem") or "").strip()
            if not item_id:
                continue
            if item_id not in maintenance_items:
                item_rows = _collection_rows(
                    _s4_get(
                        "C_MaintenanceItemTP",
                        {
                            "$filter": f"MaintenanceItem eq '{_odata_literal(item_id)}' and IsActiveEntity eq true",
                            "$format": "json",
                        },
                        service_root=S4_MAINTENANCE_SERVICE_ROOT,
                    )
                )
                maintenance_items[item_id] = item_rows[0] if item_rows else {}
            item = maintenance_items[item_id]
            production_work_center = str(item.get("WorkCenter") or "").strip()
            maintenance_plant = str(item.get("MaintenancePlant") or "").strip()
            if work_center and production_work_center.casefold() != work_center.casefold():
                continue
            if plant and maintenance_plant.casefold() != plant.casefold():
                continue

            scheduled_start_timestamp = call_object.get("MaintOrdSchedldBscStrtDateTime")
            scheduled_end_timestamp = call_object.get("MaintOrdSchedldBscEndDateTime")
            scheduled_start = (
                _date_value(scheduled_start_timestamp)
                or _date_value(call_object.get("ScheduledBasicStartDate"))
            )
            scheduled_end = (
                _date_value(scheduled_end_timestamp)
                or _date_value(call_object.get("ScheduledBasicEndDate"))
            )
            planned_date = _date_value(call.get("MaintenanceCallNextPlannedDate"))
            completion_date = _date_value(
                call_object.get("CompletionDate") or call.get("CompletionDate")
            )
            schedule_rows.append({
                "maintenance_plan": plan,
                "maintenance_plan_description": call.get("MaintenancePlanDesc"),
                "call_number": call_number,
                "maintenance_item": item_id,
                "maintenance_item_description": call_object.get("MaintenanceItemDescription") or item.get("MaintenanceItemDescription"),
                "maintenance_order": call_object.get("MaintenanceOrder"),
                "production_work_center": production_work_center or None,
                "production_plant": None,
                "maintenance_plant": maintenance_plant or None,
                "maintenance_planning_plant": item.get("MaintenancePlanningPlant"),
                "maintenance_work_center": item.get("MainWorkCenter"),
                "maintenance_work_center_plant": item.get("MainWorkCenterPlant"),
                "planned_date": planned_date,
                "scheduled_start_date": scheduled_start,
                "scheduled_start_time": (
                    _time_value(scheduled_start_timestamp)
                    or call_object.get("MaintOrdBasicStartTime")
                ),
                "scheduled_end_date": scheduled_end,
                "scheduled_end_time": (
                    _time_value(scheduled_end_timestamp)
                    or call_object.get("MaintOrdBasicEndTime")
                ),
                "scheduling_status": call_object.get("SchedulingStatus") or call.get("SchedulingStatus"),
                "scheduling_status_text": call_object.get("SchedulingStatusText") or call.get("SchedulingStatusText"),
                "is_outstanding": call.get("MaintenanceCallIsOutstanding"),
                "completion_date": completion_date,
                "completed": bool(completion_date),
            })

    schedule_rows.sort(key=lambda row: (
        row.get("scheduled_start_date") or row.get("planned_date") or "",
        row.get("production_plant") or "",
        row.get("production_work_center") or "",
    ))
    return {
        "status": "completed",
        "read_only": True,
        "evidence_type": "s4_maintenance_schedule",
        "source": "SAP S/4 maintenance plan schedule",
        "filters": {
            "start_date": start_date,
            "end_date": end_date,
            "plant": plant,
            "work_center": work_center,
        },
        "maintenance_count": len(schedule_rows),
        "unlinked_item_count": sum(not row.get("production_work_center") for row in schedule_rows),
        "maintenance_calls": schedule_rows,
        "capacity_loss_calculated": False,
    }