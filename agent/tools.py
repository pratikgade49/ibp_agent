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
import statistics
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
IBP_PLANNING_AREA = os.environ.get("IBP_PLANNING_AREA", "YCIBP1")
IBP_PERIOD_LEVEL = os.environ.get("IBP_PERIOD_LEVEL", "3")
IBP_UOM_TO_ID = os.environ.get("IBP_UOM_TO_ID", "EA")
PLANNING_DATA_PATH = (
    "/sap/opu/odata/IBP/PLANNING_DATA_API_SRV/"
    f"{IBP_PLANNING_AREA}"
)

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

# ---------------------------------------------------------------------------
# 1. Forecast vs. Consumption Alert
# ---------------------------------------------------------------------------
_MOCK_FORECAST_VS_CONSUMPTION = {
   ("1010", "Product A"): {"forecast": 10000, "actual": 13500},
}

def get_forecast_vs_consumption(
   location: str | None = None,
   product: str | None = None,
   threshold_pct: float = 20.0,
) -> dict:
   """
   Compare statistical forecast vs actual consumption. Location and product
   are optional; omitted filters return every matching combination.
   """
   analyses = []
   if USE_MOCK_DATA:
       rows = [
           {"location": row_location, "product": row_product, **values}
           for (row_location, row_product), values in _MOCK_FORECAST_VS_CONSUMPTION.items()
           if (location is None or row_location == location)
           and (product is None or row_product == product)
       ]
   else:
       filters = [
           f"UOMTOID eq '{IBP_UOM_TO_ID}'",
           f"PERIODID{IBP_PERIOD_LEVEL}_REL eq 0",
       ]
       if location:
           filters.append(f"LOCID eq '{location}'")
       if product:
           filters.append(f"PRDID eq '{product}'")
       result = _ibp_get(
           select=(
               f"PRDID,LOCID,PERIODID{IBP_PERIOD_LEVEL}_TSTAMP,"
               "UOMTOID,STATISTICALFORECASTQTY,ACTUALSQTY"
           ),
           filter_=" and ".join(filters),
       )
       rows = [
           {
               "location": row["LOCID"],
               "product": row["PRDID"],
               "forecast": float(row["STATISTICALFORECASTQTY"]),
               "actual": float(row["ACTUALSQTY"]),
           }
           for row in result.get("d", {}).get("results", [])
       ]
   for row in rows:
       forecast, actual = row["forecast"], row["actual"]
       variance_pct = (abs(actual - forecast) / forecast) * 100 if forecast else 0.0
       analyses.append(
           {
               **row,
               "variance_pct": round(variance_pct, 1),
               "alert": variance_pct > threshold_pct,
               "direction": "over-consumption" if actual > forecast else "under-consumption",
           }
       )
   response = {
       "location_filter": location,
       "product_filter": product,
       "threshold_pct": threshold_pct,
       "count": len(analyses),
       "alert_count": sum(item["alert"] for item in analyses),
       "results": analyses,
   }
   if len(analyses) == 1:
       response.update(analyses[0])
   return response

# ---------------------------------------------------------------------------
# 2. Detect Anomaly in Forecast Pattern
# ---------------------------------------------------------------------------
_MOCK_STATISTICAL_FORECAST = {
    "B1": [1000, 1050, 4200, 1100, 1080, 1120],  # spike in month 3
    "B4": [800, 800, 800, 800, 800, 800],  # flatline
    "B7": [1200, 1250, 1230, 1260, 1245, 1255],  # normal
}

def detect_forecast_anomalies(
    sigma_threshold: float = 3.0, flatline_min_periods: int = 4
) -> dict:
   """
    Scan statistical forecast time series for spikes/drops (|delta| > sigma_threshold
   standard deviations) and flatlines (>= flatline_min_periods identical
   non-zero consecutive values). Mirrors Joule skill: detectForecastAnomalies.
   The statistical detection itself runs in deterministic Python, not in the
   LLM prompt -- the agent only reasons over the structured result below.
   """
   if USE_MOCK_DATA:
       series_by_product = _MOCK_STATISTICAL_FORECAST
   else:
       result = _ibp_get(
           select=(
               f"PRDID,LOCID,PERIODID{IBP_PERIOD_LEVEL}_TSTAMP,"
               f"UOMTOID,STATISTICALFORECASTQTY"
           ),
           filter_=(
               f"UOMTOID eq '{IBP_UOM_TO_ID}' "
               f"and PERIODID{IBP_PERIOD_LEVEL}_REL ge 0"
           ),
       )
       rows = result.get("d", {}).get("results", [])
       series_by_product = {}
       for row in rows:
           series_by_product.setdefault(row["PRDID"], []).append(
               float(row["STATISTICALFORECASTQTY"])
           )
   anomalies = []
   for product_id, series in series_by_product.items():
       anomalies.extend(_find_spikes_and_drops(product_id, series, sigma_threshold))
       anomalies.extend(
           _find_flatlines(product_id, series, flatline_min_periods)
       )
   return {
       "forecast_type": "STATISTICALFORECASTQTY",
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
   mad = statistics.median(abs_devs) or 1e-9
   # 0.6745 scales MAD to be comparable to a standard deviation for normal data
   found = []
   for i, delta in enumerate(deltas):
       robust_z = 0.6745 * abs(delta - median_delta) / mad
       if robust_z > sigma_threshold:
           pct_change = (delta / series[i]) * 100 if series[i] else 0
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

def get_sales_history_status(target_period: Optional[str] = None) -> dict:
   """
   Verify historical sales data (HISTSALES) is loaded through the target
   period. Mirrors Joule skill: getSalesHistory.
   """
   if target_period is None:
       target_period = datetime.utcnow().strftime("%Y-%m")
   if USE_MOCK_DATA:
       last_loaded = _MOCK_LAST_LOADED_PERIOD
   else:
       result = _ibp_get(
           select=(
               f"PRDID,LOCID,PERIODID{IBP_PERIOD_LEVEL}_TSTAMP,"
               f"UOMTOID,HISTSALES"
           ),
           filter_=(
               f"UOMTOID eq '{IBP_UOM_TO_ID}' "
               f"and PERIODID{IBP_PERIOD_LEVEL}_REL eq 0"
           ),
       )
       rows = result.get("d", {}).get("results", [])
       period_field = f"PERIODID{IBP_PERIOD_LEVEL}_TSTAMP"
       last_loaded = rows[0][period_field][:7] if rows else None
   ready = last_loaded is not None and last_loaded >= target_period
   return {
       "target_period": target_period,
       "last_loaded_period": last_loaded,
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