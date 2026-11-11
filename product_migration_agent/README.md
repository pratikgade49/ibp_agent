# Product Master Migration Agent (Oracle -> S/4HANA)

Isolated package, same pattern as `customer_base_agent`. Host integration is two lines in `app.py`
(`include_router(migration_router)`) plus the `ProductMigration` screen in the frontend.

## What it does
Three `.xlsx` uploads: **data** (Oracle product master), **mapping** (Oracle field -> SAP table/field/length/mandatory)
and **rules** (SAP Field, SAP Description, Mandatory, Data List, Flag if). Output: one Excel workbook with
MARA / MAKT / MARC / MBEW load sheets, MATERIAL_STATUS, ISSUES, CHANGE_LOG, AI_RULE_DECISIONS, AI_VALUE_DECISIONS, SUMMARY.

## Automatic S/4 OData field matching
The mapping workbook only needs the Oracle source field to SAP technical field columns used for conversion. No
S/4 API entity, property, or navigation columns are needed. When products are pushed, the service fetches the live
`API_PRODUCT_SRV` `$metadata`, asks the LLM to match each populated Oracle source field to an available S/4 target,
and validates every returned entity/property/navigation tuple against that metadata before any create request.
The Oracle field name is the primary clue; the SAP technical field and description provide context. Unmapped or
invalid targets block the push and are reported as an upload error. Mapping choices are not displayed in the UI.

Source `MATNR` identifies and groups input rows; it must **not** map to `A_Product` / `Product`. For internal number
assignment, the root POST sends `"Product": ""` and SAP generates the key. The generated key is returned in the
upload result when SAP includes it in the response. Required properties are determined from live metadata
(`Nullable="false"` and entity keys), rather than a hard-coded list such as `ProductType`.

SAP requires a description in the root create request. It is deep-inserted under `to_Description` using the OData V2
`results` collection. Both root and description `Product` values are empty to trigger internal number assignment;
`Language` and `ProductDescription` are included. Other direct-child entities (such as plant and valuation) are
created afterward with separate POST requests to the generated Product's navigation URL. Their request bodies include
the SAP-generated `Product` and required child keys such as `Plant`. A related-entity failure is reported as a partial
product creation. Only direct navigations exposed by the live `A_Product` metadata are supported.
When source values are absent, product creation defaults `IndustrySector` to `M`, `IsMarkedForDeletion` to `false`,
and the description language to `EN` (`MIGRATION_DEFAULT_API_LANGUAGE` can override the language). A mapped source
value takes precedence. `CreationDate` is system-maintained/non-creatable and is intentionally omitted from the POST.

## How the AI is used (and where it is not)
1. `rules.py` - plain code handles Mandatory=Y and simple value lists. The LLM is called only for free-text rules
   and returns JSON in a fixed vocabulary (`not_blank, in_list, conditional_set, max_length, compare_fields, regex`).
   Output is schema-validated (unknown fields/types are rejected, one retry with the error fed back).
2. `value_maps.py` - the LLM proposes source->SAP code translations for values SAP would reject (e.g. RAW->ROH).
3. `engine.py` - deterministic, no AI: value maps -> derive rules -> checks -> table split. Same input, same output.
4. Decisions below `MIGRATION_MIN_CONFIDENCE` (default 0.7) are NOT applied; they are returned as `human_needed`.
   Deterministic rules always apply; only the AI-derived part of a rule is withheld.
5. Row data never goes to the LLM: only rule text, field names/descriptions and distinct code values to translate.
6. AI results are cached by rule text (`MIGRATION_CACHE_DIR`, default system temp), so re-runs are free.

## LLM
Uses the shared provider from `agent/llm.py` (`LLM_PROVIDER`, `BEDROCK_MODEL`, ...). `LLMClient.complete()` was added
there (text in, text out, no tools) and the four providers now omit the tools argument when the list is empty.

## API
- `POST /migration/convert` multipart: `data`, `mapping`, `rules`, optional `min_confidence`
  -> `{job_id, summary, human_needed, download_url}` (400 for bad input, 502 for provider failure)
- `POST /migration/push/{job_id}` -> automatically map populated Oracle fields against live S/4 metadata, validate the result, and create ready products
- `GET /migration/download/{job_id}` -> the workbook (kept in memory, last 20 jobs)

## Settings (env, all optional)
`MIGRATION_MIN_CONFIDENCE`, `MIGRATION_CACHE_DIR`, `MIGRATION_MAX_UPLOAD_MB` (20), `MIGRATION_MAX_ROWS` (200000),
`MIGRATION_DEFAULT_LANGUAGE` (E, legacy SAP language key), `MIGRATION_DEFAULT_API_LANGUAGE` (EN). Key fields missing from the mapping file are set in `config.py`
(`MAKT-SPRAS` constant, `MBEW-BWKEY` = plant).

## Tests
`python -m unittest discover -s product_migration_agent -t .`
