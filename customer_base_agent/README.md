# Customer Base Forecast Agent

This package is isolated from the existing IBP and S/4 agents. It does not
change their routes, imports, or deployment configuration. The host application
can integrate `run_customer_base_agent` from `customer_base_agent.graph` after
it supplies the authenticated planner identity and conversation history.

## Calculation

The numeric `ZINSCUSTOMERBASEFCSTTYPE` value in the month immediately before
the requested start is the seed. Every requested month, including the first,
is calculated recursively using that month's inputs:

```text
forecast[t] = forecast[t-1] * (1 - attrition_rate[t] / 12)
              + NCS[t] + net_conversion[t]
```

Attrition may be returned as a decimal fraction (`0.1`) or percent (`10`).
Values greater than `1` and up to `100` are normalized from percent to a
fraction before calculating; values outside `0..100` are rejected. The seed
key figure is read beginning with the seed period. Calculated values are
imported to `ZINSCOPYCUSTOMERBASEFORECAST` for requested output periods. NCS,
attrition, and net conversion are read from their configured source key figures. Missing
periods, the seed forecast, or future inputs fail the run rather than being
silently filled. For an October start, September's existing forecast seeds
October's calculation; November then uses the calculated October value.

## IBP Configuration

The connector reads its OData connection credentials from the bound Cloud
Foundry user-provided service `customer-base-ibp-credentials`. Create it in the
target org/space with these credential keys:

```json
{
   "base_url": "https://<your-ibp-host>",
   "user": "<scenario-agent-odata-user>",
   "password": "<scenario-agent-odata-password>"
}
```

The setup commands are:

```powershell
cf create-user-provided-service customer-base-ibp-credentials -p '{"base_url":"https://<your-ibp-host>","user":"<scenario-agent-odata-user>","password":"<scenario-agent-odata-password>"}'
cf bind-service IBPDemandPlanningAgent customer-base-ibp-credentials
cf restage IBPDemandPlanningAgent
```

Run these with `cf` targeted at the application's org and space. Avoid placing
real passwords in shell history; use the BTP cockpit's user-provided service
credential form or your approved secret-injection process to create/update the
service. The app must be bound to the service for its credentials to appear in
`VCAP_SERVICES`. Override the binding name with
`CUSTOMER_BASE_IBP_CREDENTIALS_SERVICE` if needed.

For local development only, the connector falls back to
`CUSTOMER_BASE_IBP_BASE_URL`, `CUSTOMER_BASE_IBP_USER`, and
`CUSTOMER_BASE_IBP_PASSWORD`. It never falls back to the existing agent's
`IBP_BASE_URL`, `IBP_USER`, or `IBP_PASSWORD`. Planning area and period level
remain separate settings: `CUSTOMER_BASE_IBP_PLANNING_AREA` defaults to
`INSPROD`, and `CUSTOMER_BASE_IBP_PERIOD_LEVEL` defaults to `3`. The package
also adds these planning-specific settings:

```powershell
cf set-env IBPDemandPlanningAgent CUSTOMER_BASE_IBP_BASE_URL "<scenario IBP OData URL>"
cf set-env IBPDemandPlanningAgent CUSTOMER_BASE_IBP_USER "<scenario OData user>"
cf set-env IBPDemandPlanningAgent CUSTOMER_BASE_IBP_PASSWORD "<set securely>"
cf set-env IBPDemandPlanningAgent CUSTOMER_BASE_IBP_PLANNING_AREA "INSPROD"
cf restage IBPDemandPlanningAgent
```

Replace the password placeholder through your approved secret-management
workflow; do not paste a real password into shell history. The application must
be authenticated with `cf login` and targeted to the intended org and space
before setting variables.

| Setting | Default | Purpose |
| --- | --- | --- |
| `CUSTOMER_BASE_DIMENSIONS` | `PRDFAMILY,CATEGORY,CUSTCOUNTRY,PATIENTTYPE` | Ordered planning-level fields |
| `CUSTOMER_BASE_KF_NCS` | `ZINSNCSTYPE` | NCS input key figure |
| `CUSTOMER_BASE_KF_ATTRITION` | `ZINSANNUALATTRITIONRATE` | Attrition Rate input key figure |
| `CUSTOMER_BASE_KF_NET_CONVERSION` | `ZINSNETCONVERSIONSTYPE` | Net Conversion input key figure |
| `CUSTOMER_BASE_KF_SEED_FORECAST` | `ZINSCUSTOMERBASEFCSTTYPE` | Numeric prior-month seed key figure; legacy `CUSTOMER_BASE_KF_FORECAST_TYPE` is also accepted |
| `CUSTOMER_BASE_KF_FORECAST` | `ZINSCOPYCUSTOMERBASEFORECAST` | Customer Base Forecast output key figure |
| `CUSTOMER_BASE_NAVIGATION_PROPERTY` | `Nav` + planning area | Planning-data import navigation property |
| `CUSTOMER_BASE_TRANSACTION_NAME` | `Customer Base Forecast Agent` | IBP import audit label |
| `CUSTOMER_BASE_API_TIMEOUT` | `60` | Request timeout in seconds |
| `CUSTOMER_BASE_RUN_DB` | `customer_base_runs.sqlite3` | Local reference run store path |

The configured base planning level is monthly period plus
`PRDFAMILY,CATEGORY,CUSTCOUNTRY,PATIENTTYPE`, with `PERIODID3_TSTAMP` used for
the month. `CATEGORY` and `CUSTCOUNTRY` are separate dimensions in the API
aggregation level. Preparation accepts an optional country code (for example,
`country="FR"`); when supplied, the IBP read, calculation, preview, and import
are all restricted to that country. Omitting country retains all returned
countries.

The planning dimensions and source/target key-figure IDs above were provided
for this planning area. `ZINSCUSTOMERBASEFCSTTYPE` supplies the numeric seed,
and `ZINSCOPYCUSTOMERBASEFORECAST` is the output key figure. No Customer Base
Actuals key figure is requested.
Verify the navigation property and period-field behavior against the target
planning area's `$metadata` and a real scenario before enabling writes.

The supplied INSPROD metadata defines `VERSIONID` and `SCENARIOID` on the
planning-data entity. IBP requires both in the read filter: `VERSIONID` is the
parent planning version, and `SCENARIOID` identifies the scenario based on that
version. The import transaction likewise receives both `VersionID` and
`ScenarioID`. The planner must supply both IDs; a scenario ID alone is not
enough to select its planning data. Validate a read and preview first, then
test write, import completion, and read-back using a disposable shared scenario
and the integration business user.

## Workflow and Safeguards

1. `prepare_customer_base_forecast` reads inputs for the requested parent
   planning version and scenario,
   validates completeness and attrition bounds, calculates results, and stores
   a preview. It does not write to IBP.
2. One prepared, importing, or submitted run per planning-version/scenario pair
   is allowed at a time. The pair lock remains held after IBP accepts the import until
   `RunStore.resolve_submitted` is called after external reconciliation.
   Different version/scenario pairs can proceed independently.
3. A write requires the exact generated approval phrase as the latest user
   message. The authenticated requester is supplied by the host application,
   never trusted from model-generated arguments.
4. The result says `import_submitted`; the current package does not poll IBP
   processing status or reconcile read-back values. The lock intentionally
   remains held until a host worker verifies completion and resolves the run.

The SQLite store and lock are a local reference implementation only. SQLite on
Cloud Foundry local disk is not shared across app instances and may not survive
restaging. Before horizontally scaling or production use, replace `RunStore`
with a shared HANA/Postgres implementation using an atomic unique active-run
constraint. Likewise, put run execution behind a durable queue/worker and add
import-status polling, retries, notification, stale-scenario cleanup, XSUAA
authorization, and admin access before production rollout.

## Local Checks

Run the package tests from the repository root:

```powershell
python -m unittest customer_base_agent.test_calculator customer_base_agent.test_run_store customer_base_agent.test_config customer_base_agent.test_connector customer_base_agent.test_service
```

The test route is `/customer-base/ask`. It currently assigns a conversation-
scoped test requester because production XSUAA authentication and planner
authorization are not wired into this application yet. Do not use the test
requester as a production security boundary.

For the Baseline planning version, provide `Baseline` (or `BASELINE`) as the
planning version ID. The service maps it to the empty `VERSIONID` required by
IBP for baseline data and preserves the scenario ID in both read and import
requests.