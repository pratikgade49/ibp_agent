"""IBP Planning Data API adapter for customer-base key figures."""

from __future__ import annotations

import uuid
from datetime import datetime
from urllib.parse import urljoin

import requests

from customer_base_agent.config import CustomerBaseConfig


class IBPConnector:
    def __init__(self, config: CustomerBaseConfig):
        self.config = config
        self.config.validate_connection()
        self.service_root = "/sap/opu/odata/IBP/PLANNING_DATA_API_SRV"
        self.data_path = f"{self.service_root}/{config.planning_area}"

    @property
    def auth(self) -> tuple[str, str]:
        return self.config.user, self.config.password

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        response = requests.request(
            method,
            url,
            auth=self.auth,
            timeout=self.config.timeout_seconds,
            **kwargs,
        )
        if not response.ok:
            raise RuntimeError(
                f"IBP request failed with HTTP {response.status_code}: {response.text[:1000]}"
            )
        return response

    def read_inputs(
        self,
        *,
        planning_version_id: str,
        scenario_id: str,
        start_period: str,
        end_period: str,
        seed_period: str,
        country: str | None = None,
    ) -> list[dict]:
        """Read the requested dimensions and calculation inputs for a scenario."""
        level = self.config.period_level
        period_field = f"PERIODID{level}_TSTAMP"
        query_start = _month_from_index(min(_month_index(start_period), _month_index(seed_period)))
        fields = [
            *self.config.dimensions,
            f"PERIODID{level}_TSTAMP",
            *(
                self.config.key_figures[name]
                for name in (
                    "ncs", "attrition_rate", "net_conversion", "seed_forecast"
                )
            ),
        ]
        filters = [
            f"{period_field} ge datetime'{query_start}-01T00:00:00'",
            f"{period_field} le datetime'{end_period}-01T00:00:00'",
            f"VERSIONID eq '{_odata_literal(planning_version_id)}'",
            f"SCENARIOID eq '{_odata_literal(scenario_id)}'",
        ]
        if country:
            if "CUSTCOUNTRY" not in self.config.dimensions:
                raise ValueError("Country filtering requires CUSTCOUNTRY in CUSTOMER_BASE_DIMENSIONS")
            filters.append(f"CUSTCOUNTRY eq '{_odata_literal(country)}'")
        url = f"{self.config.base_url}{self.data_path}"
        params = {"$select": ",".join(fields), "$filter": " and ".join(filters), "$format": "json"}
        response = self._request("GET", url, params=params)
        payload = response.json()
        data = payload.get("d", {})
        rows = list(data.get("results", []))
        next_url = data.get("__next")
        while next_url:
            page = self._request("GET", urljoin(url, next_url))
            page_data = page.json().get("d", {})
            rows.extend(page_data.get("results", []))
            next_url = page_data.get("__next")

        normalized = []
        period_timestamp = f"PERIODID{level}_TSTAMP"
        for row in rows:
            normalized.append(
                {
                    **{dimension: row.get(dimension) for dimension in self.config.dimensions},
                    "period": _period_month(row.get(period_timestamp)),
                    "ncs": _number_or_none(row.get(self.config.key_figures["ncs"])),
                    "attrition_rate": _number_or_none(
                        row.get(self.config.key_figures["attrition_rate"])
                    ),
                    "net_conversion": _number_or_none(
                        row.get(self.config.key_figures["net_conversion"])
                    ),
                    "base_forecast": _number_or_none(
                        row.get(self.config.key_figures["seed_forecast"])
                    ),
                }
            )
        return [row for row in normalized if row["period"] is not None]

    def import_forecasts(
        self,
        *,
        planning_version_id: str,
        scenario_id: str,
        rows: list[dict],
    ) -> dict:
        """Submit all forecast rows as one IBP planning-data import transaction."""
        period_field = f"PERIODID{self.config.period_level}_TSTAMP"
        aggregation_fields = [
            *self.config.dimensions,
            self.config.key_figures["forecast"],
            period_field,
        ]
        import_rows = []
        for row in rows:
            period = row["period"]
            datetime.strptime(period, "%Y-%m")
            import_row = {dimension: row[dimension] for dimension in self.config.dimensions}
            import_row[self.config.key_figures["forecast"]] = str(row["customer_base_forecast"])
            import_row[period_field] = f"{period}-01T00:00:00"
            import_rows.append(import_row)

        service_url = f"{self.config.base_url}{self.service_root}/$metadata"
        session = requests.Session()
        token_response = session.get(
            service_url,
            headers={"x-csrf-token": "fetch", "Accept": "application/xml"},
            auth=self.auth,
            timeout=self.config.timeout_seconds,
        )
        if not token_response.ok:
            raise RuntimeError(
                f"IBP CSRF token request failed with HTTP {token_response.status_code}"
            )
        csrf_token = token_response.headers.get("x-csrf-token")
        if not csrf_token:
            raise RuntimeError("IBP did not return an x-csrf-token")

        transaction_url = f"{self.config.base_url}{self.data_path}Trans"
        payload = {
            "Transactionid": uuid.uuid4().hex,
            "AggregationLevelFieldsString": ",".join(aggregation_fields),
            "DoCommit": True,
            "TransactionName": self.config.transaction_name,
            "VersionID": planning_version_id,
            "ScenarioID": scenario_id,
            self.config.navigation_property: import_rows,
        }
        response = session.post(
            transaction_url,
            json=payload,
            headers={
                "x-csrf-token": csrf_token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            auth=self.auth,
            timeout=self.config.timeout_seconds,
        )
        if not response.ok:
            raise RuntimeError(
                f"IBP import failed with HTTP {response.status_code}: {response.text[:1000]}"
            )
        return {
            "status": "import_submitted",
            "planning_version_id": planning_version_id,
            "scenario_id": scenario_id,
            "row_count": len(import_rows),
            "transaction_id": payload["Transactionid"],
            "response": response.json() if response.content else {},
        }


def _period_month(value: object) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        if len(value) >= 7 and value[4] == "-":
            return value[:7]
        if value.startswith("/Date("):
            try:
                milliseconds = int(value[6:].split(")", 1)[0])
                return datetime.utcfromtimestamp(milliseconds / 1000).strftime("%Y-%m")
            except (ValueError, OverflowError):
                return None
    return None


def _month_index(period: str) -> int:
    parsed = datetime.strptime(period, "%Y-%m")
    return parsed.year * 12 + parsed.month - 1


def _month_from_index(index: int) -> str:
    year, month_zero_based = divmod(index, 12)
    return f"{year:04d}-{month_zero_based + 1:02d}"


def _number_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _odata_literal(value: str) -> str:
    return value.replace("'", "''")