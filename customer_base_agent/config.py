"""Configuration isolated to the customer-base forecast agent."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class CustomerBaseConfig:
    base_url: str
    user: str
    password: str
    planning_area: str
    period_level: str
    dimensions: tuple[str, ...]
    key_figures: dict[str, str]
    navigation_property: str
    transaction_name: str
    timeout_seconds: float
    database_path: str

    @classmethod
    def from_env(cls) -> "CustomerBaseConfig":
        credentials = _customer_base_credentials()
        planning_area = os.environ.get("CUSTOMER_BASE_IBP_PLANNING_AREA", "INSPROD")
        dimensions = tuple(
            item.strip()
            for item in os.environ.get(
                "CUSTOMER_BASE_DIMENSIONS", "PRDFAMILY,CATEGORY,CUSTCOUNTRY,PATIENTTYPE"
            ).split(",")
            if item.strip()
        )
        return cls(
            base_url=str(
                credentials.get("base_url") or credentials.get("url") or ""
            ).rstrip("/"),
            user=str(credentials.get("user") or credentials.get("username") or ""),
            password=str(credentials.get("password") or ""),
            planning_area=planning_area,
            period_level=os.environ.get("CUSTOMER_BASE_IBP_PERIOD_LEVEL", "3"),
            dimensions=dimensions,
            key_figures={
                "ncs": os.environ.get("CUSTOMER_BASE_KF_NCS", "ZINSNCSTYPE"),
                "attrition_rate": os.environ.get(
                    "CUSTOMER_BASE_KF_ATTRITION", "ZINSANNUALATTRITIONRATE"
                ),
                "net_conversion": os.environ.get(
                    "CUSTOMER_BASE_KF_NET_CONVERSION", "ZINSNETCONVERSIONSTYPE"
                ),
                "seed_forecast": os.environ.get(
                    "CUSTOMER_BASE_KF_SEED_FORECAST",
                    os.environ.get("CUSTOMER_BASE_KF_FORECAST_TYPE", "ZINSCUSTOMERBASEFCSTTYPE"),
                ),
                "forecast": os.environ.get(
                    "CUSTOMER_BASE_KF_FORECAST", "ZINSCOPYCUSTOMERBASEFORECAST"
                ),
            },
            navigation_property=os.environ.get(
                "CUSTOMER_BASE_NAVIGATION_PROPERTY", f"Nav{planning_area}"
            ),
            transaction_name=os.environ.get(
                "CUSTOMER_BASE_TRANSACTION_NAME", "Customer Base Forecast Agent"
            ),
            timeout_seconds=float(os.environ.get("CUSTOMER_BASE_API_TIMEOUT", "60")),
            database_path=os.environ.get("CUSTOMER_BASE_RUN_DB", "customer_base_runs.sqlite3"),
        )

    def validate_connection(self) -> None:
        missing = [
            name
            for name, value in (
                ("CUSTOMER_BASE_IBP_BASE_URL", self.base_url),
                ("CUSTOMER_BASE_IBP_USER", self.user),
                ("CUSTOMER_BASE_IBP_PASSWORD", self.password),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing IBP connection settings: {', '.join(missing)}")
        if not self.dimensions:
            raise RuntimeError("CUSTOMER_BASE_DIMENSIONS must contain at least one dimension")


def _customer_base_credentials() -> dict:
    service_name = os.environ.get(
        "CUSTOMER_BASE_IBP_CREDENTIALS_SERVICE", "customer-base-ibp-credentials"
    )
    raw_bindings = os.environ.get("VCAP_SERVICES", "")
    if raw_bindings:
        try:
            bindings = json.loads(raw_bindings)
        except json.JSONDecodeError as exc:
            raise RuntimeError("VCAP_SERVICES is not valid JSON") from exc
        for services in bindings.values():
            if not isinstance(services, list):
                continue
            for service in services:
                if service.get("name") != service_name and service.get("instance_name") != service_name:
                    continue
                credentials = service.get("credentials")
                if not isinstance(credentials, dict):
                    raise RuntimeError(
                        f"Bound service {service_name!r} has no credentials object"
                    )
                return credentials

    environment_credentials = {
        "base_url": os.environ.get("CUSTOMER_BASE_IBP_BASE_URL", ""),
        "user": os.environ.get("CUSTOMER_BASE_IBP_USER", ""),
        "password": os.environ.get("CUSTOMER_BASE_IBP_PASSWORD", ""),
    }
    if any(environment_credentials.values()):
        return environment_credentials
    return {}