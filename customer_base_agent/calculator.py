"""Deterministic customer-base forecast calculations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation


class ForecastInputError(ValueError):
    """Raised when the input periods or key figures cannot be calculated."""


def _month_index(period: str) -> int:
    try:
        parsed = datetime.strptime(period, "%Y-%m")
    except (TypeError, ValueError) as exc:
        raise ForecastInputError(f"Invalid month {period!r}; expected YYYY-MM") from exc
    return parsed.year * 12 + parsed.month - 1


def _month_from_index(index: int) -> str:
    year, month_zero_based = divmod(index, 12)
    return f"{year:04d}-{month_zero_based + 1:02d}"


def _decimal(value: object, field: str, period: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ForecastInputError(
            f"{field} must be numeric for {period}; got {value!r}"
        ) from exc
    if not result.is_finite():
        raise ForecastInputError(f"{field} must be finite for {period}")
    return result


def calculate_customer_base_forecast(
    rows: list[dict],
    *,
    seed_period: str,
    start_period: str,
    end_period: str,
) -> list[dict]:
    """Use the existing forecast key figure through the seed month, then roll forward.

    Future formula: prior-period customer-base forecast *
    (1 - annual attrition rate / 12) + NCS + net conversions. Attrition rates
    are decimal fractions, so 0.1 means 10% annually.
    """
    seed_index = _month_index(seed_period)
    start_index = _month_index(start_period)
    end_index = _month_index(end_period)
    if start_index > end_index:
        raise ForecastInputError("start_period must not be after end_period")

    values_by_period: dict[str, dict] = {}
    for row in rows:
        period = row.get("period")
        _month_index(period)
        if period in values_by_period:
            raise ForecastInputError(f"Duplicate input row for {period}")
        values_by_period[period] = row

    first_index = min(start_index, seed_index)
    calculated: dict[str, Decimal] = {}
    output = []
    for month_index in range(first_index, end_index + 1):
        period = _month_from_index(month_index)
        row = values_by_period.get(period)
        if row is None:
            raise ForecastInputError(f"Missing input row for {period}")

        if month_index <= seed_index:
            base_forecast = row.get("base_forecast")
            if base_forecast is None:
                raise ForecastInputError(
                    f"Customer Base Forecast is missing for seed period {period}"
                )
            value = _decimal(base_forecast, "base_forecast", period)
        else:
            previous_period = _month_from_index(month_index - 1)
            previous_value = calculated.get(previous_period)
            if previous_value is None:
                raise ForecastInputError(
                    f"Cannot forecast {period}; prior-period base {previous_period} is missing"
                )
            for field in ("ncs", "attrition_rate", "net_conversion"):
                if row.get(field) is None:
                    raise ForecastInputError(f"{field} is missing for {period}")
            ncs = _decimal(row["ncs"], "ncs", period)
            annual_attrition = _decimal(row["attrition_rate"], "attrition_rate", period)
            net_conversion = _decimal(row["net_conversion"], "net_conversion", period)
            value = (
                previous_value * (Decimal("1") - annual_attrition / Decimal("12"))
                + ncs
                + net_conversion
            )

        calculated[period] = value
        if start_index <= month_index <= end_index:
            result = {
                key: item
                for key, item in row.items()
                if key not in {
                    "period", "base_forecast", "ncs", "attrition_rate",
                    "net_conversion", "seed_forecast",
                }
            }
            result.update({"period": period, "customer_base_forecast": round(float(value), 2)})
            output.append(result)

    return output