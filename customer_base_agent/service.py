"""Customer-base forecast validation, calculation, and approval workflow."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
import json

from customer_base_agent.calculator import calculate_customer_base_forecast
from customer_base_agent.config import CustomerBaseConfig
from customer_base_agent.ibp_connector import IBPConnector
from customer_base_agent.run_store import RunStore


def prepare_forecast(
    *,
    planning_version_id: str,
    scenario_id: str,
    requester: str,
    start_period: str,
    end_period: str,
    country: str | None = None,
    config: CustomerBaseConfig | None = None,
    connector: IBPConnector | None = None,
    store: RunStore | None = None,
) -> dict:
    planning_version_id = _normalize_planning_version_id(planning_version_id)
    if not scenario_id.strip():
        raise ValueError("scenario_id is required")
    if not requester.strip():
        raise ValueError("requester is required")
    if country is not None:
        country = country.strip()
        if not country:
            raise ValueError("country must not be empty when supplied")
    datetime.strptime(start_period, "%Y-%m")
    datetime.strptime(end_period, "%Y-%m")
    start_date = datetime.strptime(start_period, "%Y-%m")
    seed_period = (start_date.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    config = config or CustomerBaseConfig.from_env()
    if country and "CUSTCOUNTRY" not in config.dimensions:
        raise ValueError("Country filtering requires CUSTCOUNTRY in CUSTOMER_BASE_DIMENSIONS")
    connector = connector or IBPConnector(config)
    store = store or RunStore(config.database_path)
    raw_rows = connector.read_inputs(
        planning_version_id=planning_version_id,
        scenario_id=scenario_id,
        start_period=start_period,
        end_period=end_period,
        seed_period=seed_period,
        country=country,
    )
    if country:
        raw_rows = [row for row in raw_rows if str(row.get("CUSTCOUNTRY", "")) == country]
    if not raw_rows:
        raise ValueError(
            "IBP returned no input rows for this version and period range. "
            "Check scenario sharing, version ID, planning area, and key-figure IDs."
        )

    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in raw_rows:
        dimensions = tuple(row.get(dimension) for dimension in config.dimensions)
        if any(value in (None, "") for value in dimensions):
            raise ValueError(f"Input row has missing planning dimensions: {row}")
        grouped[dimensions].append(row)

    calculated_rows = []
    warnings = []
    for dimension_values, series in grouped.items():
        for row in series:
            if row["period"] <= seed_period or row.get("attrition_rate") is None:
                continue
            rate = float(row["attrition_rate"])
            if not 0 <= rate <= 100:
                raise ValueError(
                    f"Attrition Rate outside 0..100% for {row['period']}: {rate}"
                )
            if rate > 1:
                row["attrition_rate"] = rate / 100

        forecast = calculate_customer_base_forecast(
            series,
            seed_period=seed_period,
            start_period=start_period,
            end_period=end_period,
        )
        for result in forecast:
            result.update(dict(zip(config.dimensions, dimension_values)))
            if result["customer_base_forecast"] < 0:
                warnings.append(
                    f"Calculated customer base is negative for {dimension_values} "
                    f"in {result['period']}"
                )
            calculated_rows.append(result)

    request_data = {
        "planning_version_id": planning_version_id,
        "scenario_id": scenario_id,
        "start_period": start_period,
        "end_period": end_period,
        "seed_period": seed_period,
        "seed_key_figure": config.key_figures["seed_forecast"],
        "country": country,
        "dimensions": list(config.dimensions),
        "forecast_rows": calculated_rows,
    }
    fingerprint = hashlib.sha256(
        json.dumps(request_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12].upper()
    approval_phrase = f"APPROVE CUSTOMER BASE RUN {fingerprint}"
    run = store.create(
        planning_version_id,
        scenario_id,
        requester,
        request_data,
        {"forecast_rows": calculated_rows, "warnings": warnings},
    )
    return {
        "status": "prepared",
        "run_id": run["run_id"],
        "planning_version_id": planning_version_id,
        "scenario_id": scenario_id,
        "country": country,
        "requester": requester,
        "row_count": len(calculated_rows),
        "forecast_rows": calculated_rows,
        "warnings": warnings,
        "approval_phrase": approval_phrase,
        "note": "No IBP write has been submitted. Exact approval is required.",
    }


def commit_forecast(
    *,
    requester: str,
    approval_phrase: str,
    user_message: str,
    config: CustomerBaseConfig | None = None,
    connector: IBPConnector | None = None,
    store: RunStore | None = None,
) -> dict:
    if not approval_phrase or approval_phrase.strip() != user_message.strip():
        raise PermissionError("Approval must exactly match the planner's latest message")
    config = config or CustomerBaseConfig.from_env()
    connector = connector or IBPConnector(config)
    store = store or RunStore(config.database_path)
    matching_runs = [
        candidate
        for candidate in store.prepared_runs_for_requester(requester)
        if _approval_phrase(candidate["request"]) == approval_phrase.strip()
    ]
    if not matching_runs:
        raise ValueError("No prepared run for this requester matches the approval phrase")
    if len(matching_runs) > 1:
        raise ValueError("Approval phrase matches multiple prepared runs")
    run = matching_runs[0]
    run_id = run["run_id"]
    request_data = run["request"]
    store.transition(run_id, requester, "prepared", "importing")
    try:
        result = connector.import_forecasts(
            planning_version_id=run["planning_version_id"],
            scenario_id=run["scenario_id"],
            rows=request_data["forecast_rows"],
        )
    except Exception as exc:
        store.transition(run_id, requester, "importing", "failed", error=str(exc)[:1000])
        raise
    store.transition(
        run_id,
        requester,
        "importing",
        "submitted",
        result=result,
    )
    return {
        "status": "import_submitted",
        "run_id": run_id,
        "planning_version_id": run["planning_version_id"],
        "scenario_id": run["scenario_id"],
        **result,
        "note": "IBP accepted the import request; completion and read-back reconciliation are not yet verified.",
    }


def _approval_phrase(request_data: dict) -> str:
    fingerprint = hashlib.sha256(
        json.dumps(request_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12].upper()
    return f"APPROVE CUSTOMER BASE RUN {fingerprint}"


def _normalize_planning_version_id(planning_version_id: str) -> str:
    value = planning_version_id.strip()
    if value.casefold() in {"baseline", "base"}:
        return ""
    if not value:
        raise ValueError("planning_version_id is required; use BASELINE for the baseline version")
    return value