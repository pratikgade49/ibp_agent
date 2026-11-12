"""Create products through explicitly mapped and metadata-validated OData fields."""
from collections import defaultdict
from difflib import SequenceMatcher
import json
import os
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, unquote

import requests

from agent.s4_capacity_tools import _s4_request_config
from .util import extract_json

PRODUCT_SERVICE_ROOT = "/sap/opu/odata/sap/API_PRODUCT_SRV"
ERROR_TEXT_LIMIT = 800
KEY_FIELD = "MATNR"
ROOT_ENTITY_SET = "A_Product"
ROOT_KEY_PROPERTY = "Product"
DEFAULT_LANGUAGE = os.environ.get("MIGRATION_DEFAULT_LANGUAGE", "E")
DEFAULT_PRODUCT_CREATE_FIELDS = {
    "IndustrySector": "M",
    "IsMarkedForDeletion": False,
}
DEFAULT_PRODUCT_DESCRIPTION_LANGUAGE = os.environ.get(
    "MIGRATION_DEFAULT_API_LANGUAGE", "EN"
).strip() or "EN"


def _missing_mapping_message(records: list[dict]) -> str:
    populated = sorted({
        field
        for row in records
        for field, value in row.items()
        if field != KEY_FIELD and value not in (None, "")
    })
    field_list = ", ".join(populated[:40]) or "(no non-key fields found)"
    if len(populated) > 40:
        field_list += f", ... and {len(populated) - 40} more"
    return (
        "S/4 upload is blocked because no AI-generated metadata mapping is available for populated SAP fields: "
        + field_list + ". "
        "MATNR is used only to identify source rows; SAP generates the Product key. "
        "Mappings are generated from Oracle source fields and validated against API_PRODUCT_SRV metadata."
    )


def _response_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error", {})
        if not isinstance(error, dict):
            error = {}
        message = error.get("message", {})
        if isinstance(message, dict):
            message = message.get("value", "")
        if message:
            parts = []
            if error.get("code"):
                parts.append(str(error["code"]))
            parts.append(str(message))
            inner = error.get("innererror", {})
            if isinstance(inner, dict):
                for detail in inner.get("errordetails", []):
                    if not isinstance(detail, dict) or not detail.get("message"):
                        continue
                    detail_text = str(detail["message"])
                    if detail.get("code"):
                        detail_text = f"{detail['code']}: {detail_text}"
                    if detail_text not in parts:
                        parts.append(detail_text)
                if inner.get("transactionid"):
                    parts.append(f"transactionid={inner['transactionid']}")
                if inner.get("timestamp"):
                    parts.append(f"timestamp={inner['timestamp']}")
            return " | ".join(parts)[:ERROR_TEXT_LIMIT]
    except (ValueError, AttributeError):
        pass
    return (response.text or f"HTTP {response.status_code}")[:ERROR_TEXT_LIMIT]


def _generated_product_id(response: requests.Response) -> str | None:
    try:
        payload = response.json()
        data = payload.get("d", payload) if isinstance(payload, dict) else {}
        if isinstance(data, dict):
            product_id = data.get(ROOT_KEY_PROPERTY)
            if product_id:
                return str(product_id)
            entity_id = data.get("__metadata", {}).get("id", "")
            match = re.search(r"A_Product\('((?:[^']|'')*)'\)", str(entity_id))
            if match:
                return match.group(1).replace("''", "'")
    except (ValueError, AttributeError):
        pass

    for header_name in ("OData-EntityId", "Location"):
        header_value = response.headers.get(header_name, "")
        match = re.search(r"A_Product\('((?:[^']|'')*)'\)", str(header_value))
        if match:
            return unquote(match.group(1)).replace("''", "'")

    content = getattr(response, "content", b"")
    if content:
        try:
            root = ET.fromstring(content)
            for node in root.iter():
                if _local_name(node.tag) == ROOT_KEY_PROPERTY and node.text:
                    return node.text.strip()
        except ET.ParseError:
            pass
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _metadata_model(xml_content: bytes) -> dict:
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise RuntimeError("S/4 API_PRODUCT_SRV returned unreadable $metadata XML") from exc

    entity_types = {}
    entity_sets = {}
    entity_set_creatable = {}
    associations = {}
    for schema in (node for node in root.iter() if _local_name(node.tag) == "Schema"):
        namespace = schema.attrib.get("Namespace", "")
        for node in schema:
            name = node.attrib.get("Name", "")
            if _local_name(node.tag) == "EntityType":
                properties = {}
                required_properties = set()
                navigations = {}
                keys = []
                for child in node:
                    child_name = _local_name(child.tag)
                    if child_name == "Property":
                        property_name = child.attrib.get("Name", "")
                        properties[property_name] = child.attrib.get("Type", "")
                        if child.attrib.get("Nullable", "true").casefold() == "false":
                            required_properties.add(property_name)
                    elif child_name == "NavigationProperty":
                        navigations[child.attrib.get("Name", "")] = {
                            "relationship": child.attrib.get("Relationship", ""),
                            "to_role": child.attrib.get("ToRole", ""),
                        }
                    elif child_name == "Key":
                        keys = [
                            ref.attrib.get("Name", "")
                            for ref in child if _local_name(ref.tag) == "PropertyRef"
                        ]
                entity_types[f"{namespace}.{name}"] = {
                    "properties": properties, "required_properties": required_properties,
                    "navigations": navigations, "keys": keys,
                }
            elif _local_name(node.tag) == "Association":
                ends = {
                    child.attrib.get("Role", ""): child.attrib.get("Type", "")
                    for child in node if _local_name(child.tag) == "End"
                }
                associations[f"{namespace}.{name}"] = ends
            elif _local_name(node.tag) == "EntityContainer":
                for child in node:
                    if _local_name(child.tag) == "EntitySet":
                        entity_set = child.attrib.get("Name", "")
                        entity_sets[entity_set] = child.attrib.get("EntityType", "")
                        capability = next(
                            (value for key, value in child.attrib.items() if _local_name(key) == "creatable"),
                            None,
                        )
                        if capability is not None:
                            entity_set_creatable[entity_set] = capability.casefold() != "false"

    if ROOT_ENTITY_SET not in entity_sets:
        raise RuntimeError(f"S/4 metadata does not expose entity set {ROOT_ENTITY_SET}")
    root_type_name = entity_sets[ROOT_ENTITY_SET]
    if root_type_name not in entity_types:
        raise RuntimeError(f"S/4 metadata does not define entity type {root_type_name}")
    root_type = entity_types[root_type_name]
    root_type["required_properties"].discard(ROOT_KEY_PROPERTY)
    root_type["generated_properties"] = {ROOT_KEY_PROPERTY}

    entity_set_by_type = {entity_type: entity_set for entity_set, entity_type in entity_sets.items()}
    nav_targets = {}
    for navigation, details in entity_types[root_type_name]["navigations"].items():
        ends = associations.get(details["relationship"], {})
        target_type = ends.get(details["to_role"], "")
        nav_targets[navigation] = entity_set_by_type.get(target_type, "")
    target_options = []
    for prop in root_type["properties"]:
        if prop == ROOT_KEY_PROPERTY:
            continue
        target_options.append({"entity": ROOT_ENTITY_SET, "property": prop, "navigation": ""})
    for navigation, entity_set in nav_targets.items():
        child_type_name = entity_sets.get(entity_set)
        child_type = entity_types.get(child_type_name)
        if not child_type:
            continue
        for prop in child_type["properties"]:
            if prop == ROOT_KEY_PROPERTY:
                continue
            target_options.append({"entity": entity_set, "property": prop, "navigation": navigation})
    return {
        "entity_types": entity_types,
        "entity_sets": entity_sets,
        "entity_set_creatable": entity_set_creatable,
        "root_creatable": entity_set_creatable.get(ROOT_ENTITY_SET),
        "root_type": root_type_name,
        "root_navigations": nav_targets,
        "target_options": target_options,
    }


def _tentative_target(target: dict, options: list[dict]) -> dict | None:
    sources = [target.get("oracle_field", ""), target.get("sap_description", ""), target.get("field", "")]

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).casefold())

    def score(candidate: dict) -> float:
        target_text = " ".join((candidate["property"], candidate["entity"], candidate["navigation"]))
        normalized_target = normalize(target_text)
        candidate_tokens = set(re.findall(r"[a-z0-9]+", target_text.casefold()))
        best = 0.0
        for source in sources:
            source = str(source or "")
            normalized_source = normalize(source)
            if not normalized_source:
                continue
            source_tokens = set(re.findall(r"[a-z0-9]+", source.casefold()))
            overlap = len(source_tokens & candidate_tokens) / max(len(source_tokens), len(candidate_tokens), 1)
            best = max(best, SequenceMatcher(None, normalized_source, normalized_target).ratio(), overlap)
            if normalized_source == normalize(candidate["property"]):
                best = 1.0
        return best

    ranked = sorted(options, key=score, reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    confidence = score(best)
    return {**best, "score": round(confidence, 2)} if confidence >= 0.2 else None


def _map_source_fields(field_map: list[dict], records: list[dict], metadata: dict, llm) -> list[dict]:
    populated = {
        field for row in records for field, value in row.items()
        if field != KEY_FIELD and value not in (None, "")
    }
    sources = {}
    for item in field_map:
        field = str(item.get("field", "")).strip()
        oracle_field = str(item.get("oracle_field", "")).strip()
        if field in populated and oracle_field:
            sources[field] = {
                "field": field,
                "oracle_field": oracle_field,
                "sap_description": str(item.get("sap_description", "")).strip(),
            }
    if not sources:
        return []

    user = (
        "Map each source to exactly one matching S/4 target using the Oracle field name as the primary clue. "
        "The SAP technical field and description are supporting context. Use only exact entity, property, "
        "and navigation values from the metadata options. Do not map the source material key. Do not guess; "
        "if a source cannot be mapped confidently, omit it. Return JSON only in this shape: "
        '{"mappings":[{"field":"SAP technical field","oracle_field":"Oracle source field",'
        '"entity":"metadata entity set","property":"metadata property","navigation":"metadata navigation or empty"}]}\n'
        f"Source fields: {json.dumps(list(sources.values()))}\n"
        f"S/4 metadata options: {json.dumps(metadata['target_options'])}"
    )
    try:
        from agent.llm import get_llm_client
        llm = llm or get_llm_client()
        response = extract_json(llm.complete(
            "You map Oracle product fields to the live SAP S/4HANA API metadata. Return one JSON object only.",
            user,
            4000,
        ))
    except Exception as exc:  # noqa: BLE001 - malformed model output must not reach SAP
        raise ValueError(f"The AI could not map Oracle fields to S/4 metadata: {exc}") from exc

    options = {
        (option["entity"], option["property"], option["navigation"])
        for option in metadata["target_options"]
    }
    mapped = {}
    rejected = set()
    for item in response.get("mappings", []):
        if not isinstance(item, dict):
            continue
        field = str(item.get("field", "")).strip()
        oracle_field = str(item.get("oracle_field", "")).strip()
        target = {
            "field": field,
            "oracle_field": oracle_field,
            "sap_description": sources.get(field, {}).get("sap_description", ""),
            "entity": str(item.get("entity", "")).strip(),
            "property": str(item.get("property", "")).strip(),
            "navigation": str(item.get("navigation", "")).strip(),
        }
        if field not in sources or oracle_field != sources[field]["oracle_field"]:
            continue
        if field in mapped or field in rejected:
            mapped.pop(field, None)
            rejected.add(field)
            continue
        if (target["entity"], target["property"], target["navigation"]) not in options:
            rejected.add(field)
            continue
        mapped[field] = target

    description_target = next((
        option for option in metadata["target_options"]
        if option["property"] == "ProductDescription"
        and option["navigation"] == "to_Description"
    ), None)
    if description_target and not any(
        target["property"] == "ProductDescription" for target in mapped.values()
    ):
        description_sources = []
        for field, source in sources.items():
            if field in mapped:
                continue
            descriptors = (
                field, source["oracle_field"], source["sap_description"],
            )
            normalized = [
                re.sub(r"[^a-z0-9]", "", value.casefold())
                for value in descriptors if value
            ]
            if any(
                value == "maktx"
                or "description" in value
                or "productname" in value
                or "materialtext" in value
                or "shorttext" in value
                for value in normalized
            ):
                description_sources.append((field, source))
        if len(description_sources) == 1:
            field, source = description_sources[0]
            mapped[field] = {
                **source,
                **description_target,
            }
    return list(mapped.values())


def _field_map_report(field_map: list[dict], records: list[dict], metadata: dict) -> dict:
    mappings = []
    normalized = defaultdict(list)
    available_fields = set().union(*(row.keys() for row in records)) if records else set()
    populated_fields = {
        field for row in records for field, value in row.items()
        if field != KEY_FIELD and value not in (None, "")
    }
    for target in field_map or []:
        sap_field = str(target.get("field", "")).strip()
        entity = str(target.get("entity", "")).strip()
        prop = str(target.get("property", "")).strip()
        navigation = str(target.get("navigation", "")).strip()
        message = ""
        mapping_status = "matched"
        if sap_field == KEY_FIELD:
            mapping_status = "optional_unmapped"
            message = f"Source {KEY_FIELD} identifies this input row; S/4 {ROOT_KEY_PROPERTY} is generated by SAP and must not be mapped"
        elif sap_field and sap_field not in populated_fields and not entity and not prop:
            mapping_status = "not_populated"
            message = "This optional field has no values to upload"
        elif sap_field and sap_field != KEY_FIELD and sap_field in populated_fields and not entity and not prop:
            mapping_status = "optional_unmapped"
            message = "Optional value will be omitted; select a target to send it"
        elif not sap_field or sap_field not in available_fields:
            message = f"Unknown converted SAP field {sap_field!r}"
        elif not entity or not prop:
            message = "S/4 API Entity and S/4 API Property are required"
        elif entity not in metadata["entity_sets"]:
            message = f"Entity set {entity!r} does not exist in API_PRODUCT_SRV metadata"
        else:
            entity_type_name = metadata["entity_sets"][entity]
            entity_type = metadata["entity_types"].get(entity_type_name)
            if entity_type is None or prop not in entity_type["properties"]:
                message = f"Property {prop!r} does not exist on entity set {entity!r}"
            elif prop == ROOT_KEY_PROPERTY:
                message = f"{ROOT_KEY_PROPERTY} is generated by SAP and must not be mapped"
            elif entity == ROOT_ENTITY_SET and navigation:
                message = "Root entity fields must not specify a navigation property"
            elif entity != ROOT_ENTITY_SET and metadata["root_navigations"].get(navigation) != entity:
                message = f"Entity set {entity!r} is not the target of A_Product navigation {navigation!r}"

        if message and mapping_status != "not_populated":
            mapping_status = "optional_unmapped"
        mappings.append({
            "field": sap_field,
            "oracle_field": str(target.get("oracle_field", "")).strip(),
            "sap_description": str(target.get("sap_description", "")).strip(),
            "entity": entity,
            "property": prop,
            "navigation": navigation,
            "status": mapping_status,
            "message": message,
            "suggestion": _tentative_target(target, metadata["target_options"]),
        })
        if mapping_status == "matched":
            normalized[sap_field].append({
                "entity": entity,
                "property": prop,
                "navigation": navigation,
                "type": entity_type["properties"][prop],
                "keys": entity_type["keys"],
            })

    root_required = metadata["entity_types"][metadata["root_type"]]["required_properties"]
    mapped_required = {
        target["property"]
        for targets in normalized.values()
        for target in targets
        if target["entity"] == ROOT_ENTITY_SET
    }
    for prop in sorted(root_required - mapped_required):
        if prop == ROOT_KEY_PROPERTY:
            continue
        mappings.append({
            "field": f"S/4 required property: {prop}", "oracle_field": "",
            "sap_description": "Required by API metadata", "entity": ROOT_ENTITY_SET,
            "property": prop, "navigation": "", "status": "unmatched",
            "message": f"Required A_Product property {prop!r} has no selected source field",
            "suggestion": None,
        })

    represented = {item["field"] for item in mappings}
    populated = {
        field for row in records for field, value in row.items()
        if field != KEY_FIELD and value not in (None, "")
    }
    for field in sorted(populated - represented):
        mappings.append({
            "field": field, "oracle_field": "", "sap_description": "",
            "entity": "", "property": "", "navigation": "",
            "status": "optional_unmapped", "message": "Optional value will be omitted; select a target to send it",
            "suggestion": None,
        })

    unmatched = sum(item["status"] == "unmatched" for item in mappings)
    root_creatable = metadata.get("root_creatable")
    return {
        "ready": unmatched == 0 and root_creatable is not False,
        "matched": sum(item["status"] == "matched" for item in mappings),
        "unmatched": unmatched,
        "optional_unmapped": sum(item["status"] == "optional_unmapped" for item in mappings),
        "not_populated": sum(item["status"] == "not_populated" for item in mappings),
        "create_allowed": root_creatable,
        "create_message": (
            f"{ROOT_ENTITY_SET} is marked non-creatable in the live API metadata"
            if root_creatable is False else ""
        ),
        "mappings": mappings,
        "target_options": metadata["target_options"],
    }


def _validate_field_map(field_map: list[dict], records: list[dict], metadata: dict) -> dict[str, list[dict]]:
    report = _field_map_report(field_map, records, metadata)
    if not report["ready"]:
        details = "; ".join(
            f"{item['field']}: {item['message']}"
            for item in report["mappings"] if item["status"] == "unmatched"
        )
        raise ValueError(details or _missing_mapping_message(records))
    normalized = defaultdict(list)
    for item in report["mappings"]:
        if item["status"] != "matched":
            continue
        entity_type_name = metadata["entity_sets"][item["entity"]]
        entity_type = metadata["entity_types"][entity_type_name]
        normalized[item["field"]].append({
            "entity": item["entity"],
            "property": item["property"],
            "navigation": item["navigation"],
            "type": entity_type["properties"][item["property"]],
            "keys": entity_type["keys"],
        })
    return normalized


def check_product_mapping(records: list[dict], field_map: list[dict] | None = None) -> dict:
    """Check explicit mappings against the live service metadata without writing data."""
    if not field_map:
        fields = sorted({
            field for row in records for field, value in row.items()
            if field != KEY_FIELD and value not in (None, "")
        })
        mappings = [{
            "field": field, "oracle_field": "", "sap_description": "",
            "entity": "", "property": "", "navigation": "",
            "status": "unmatched", "message": "No explicit API mapping provided",
            "suggestion": None,
        } for field in fields]
        return {
            "ready": False, "matched": 0, "unmatched": len(mappings),
            "optional_unmapped": 0, "not_populated": 0,
            "create_allowed": None, "create_message": "",
            "mappings": mappings, "target_options": [],
        }

    base_url, request_options = _s4_request_config()
    metadata_options = dict(request_options)
    metadata_options["headers"] = {
        **request_options.get("headers", {}),
        "Accept": "application/json",
    }
    entity_response = requests.get(
        f"{base_url}{PRODUCT_SERVICE_ROOT}/{ROOT_ENTITY_SET}",
        params={"$top": 0, "$format": "json"},
        **metadata_options,
    )
    if not entity_response.ok:
        raise RuntimeError(
            f"S/4 entity-set check failed for {PRODUCT_SERVICE_ROOT}/{ROOT_ENTITY_SET} "
            f"with HTTP {entity_response.status_code}"
        )
    metadata_options["headers"] = {
        **request_options.get("headers", {}),
        "Accept": "application/xml",
    }
    response = requests.get(
        f"{base_url}{PRODUCT_SERVICE_ROOT}/$metadata",
        **metadata_options,
    )
    if not response.ok:
        raise RuntimeError(f"S/4 API_PRODUCT_SRV metadata request failed with HTTP {response.status_code}")
    return _field_map_report(field_map, records, _metadata_model(response.content))


def _json_value(value: object, edm_type: str, field: str) -> object:
    text = str(value).strip()
    if edm_type == "Edm.Boolean":
        if text.casefold() in {"true", "x", "y", "1"}:
            return True
        if text.casefold() in {"false", "", "n", "0"}:
            return False
        raise ValueError(f"{field}: {text!r} is not a valid Boolean value")
    if edm_type in {"Edm.Byte", "Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.SByte"}:
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{field}: {text!r} is not a valid integer for {edm_type}") from exc
    if edm_type == "Edm.Decimal":
        try:
            number = Decimal(text)
            if not number.is_finite():
                raise InvalidOperation
            return str(number)
        except InvalidOperation as exc:
            raise ValueError(f"{field}: {text!r} is not a valid number for {edm_type}") from exc
    if edm_type in {"Edm.Double", "Edm.Single"}:
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{field}: {text!r} is not a valid number for {edm_type}") from exc
    return text


def _row_payload(row: dict, entity: str, navigation: str, field_map: dict[str, list[dict]]) -> dict:
    payload = {}
    for source_field, targets in field_map.items():
        for target in targets:
            if target["entity"] != entity or target["navigation"] != navigation:
                continue
            value = row.get(source_field)
            if value in (None, ""):
                continue
            prop = target["property"]
            converted = _json_value(value, target["type"], source_field)
            if prop in payload and payload[prop] != converted:
                raise ValueError(f"Conflicting source values map to {entity}.{prop}")
            payload[prop] = converted
    return payload


def push_products(records: list[dict], error_materials: set[str] | None = None,
                  field_map: list[dict] | None = None, max_records: int = 500, llm=None) -> dict:
    """Create products with AI mappings verified against live OData metadata."""
    if len(records) > max_records:
        raise ValueError(f"This push contains {len(records)} rows; the limit is {max_records}.")
    populated = {
        field for row in records for field, value in row.items()
        if field != KEY_FIELD and value not in (None, "")
    }
    error_materials = {str(value).strip() for value in (error_materials or set())}
    grouped = defaultdict(list)
    for row in records:
        material = str(row.get(KEY_FIELD, "")).strip()
        if material:
            grouped[material].append(row)

    base_url, request_options = _s4_request_config()
    session = requests.Session()
    root_url = f"{base_url}{PRODUCT_SERVICE_ROOT}"
    metadata_options = dict(request_options)
    metadata_options["headers"] = {
        **request_options.get("headers", {}),
        "x-csrf-token": "Fetch",
        "Accept": "application/xml",
    }
    token_response = session.get(f"{root_url}/$metadata", **metadata_options)
    if not token_response.ok:
        raise RuntimeError(f"S/4 API_PRODUCT_SRV metadata/CSRF request failed with HTTP {token_response.status_code}")
    csrf_token = token_response.headers.get("x-csrf-token")
    if not csrf_token:
        raise RuntimeError("S/4 did not return an x-csrf-token")
    metadata = _metadata_model(token_response.content)
    if metadata.get("root_creatable") is False:
        raise ValueError(f"S/4 metadata marks {ROOT_ENTITY_SET} as non-creatable")
    field_map = _map_source_fields(field_map or [], records, metadata, llm)
    # WERKS identifies the plant; do not depend on a model guessing this key.
    if any(row.get("DISLS") not in (None, "") for row in records):
        plant_target = next((option for option in metadata["target_options"] if option["navigation"] == "to_Plant" and option["property"] == "Plant"), None)
        plant_source = next((item for item in (field_map or []) if item.get("field") == "WERKS"), None)
        if plant_target and any(row.get("WERKS") not in (None, "") for row in records):
            field_map = [item for item in field_map if item.get("field") != "WERKS"]
            field_map.append({"field": "WERKS", "oracle_field": (plant_source or {}).get("oracle_field", ""), **plant_target})
    # DISLS is sent to the nested supply-planning entity below, not a guessed direct target.
    field_map = [item for item in field_map if item.get("field") != "DISLS"]
    validated_map = _validate_field_map(field_map, records, metadata)

    entity_response = session.get(
        f"{root_url}/{ROOT_ENTITY_SET}",
        params={"$top": 0, "$format": "json"},
        **request_options,
    )
    if not entity_response.ok:
        raise RuntimeError(
            f"S/4 entity-set check failed for {PRODUCT_SERVICE_ROOT}/{ROOT_ENTITY_SET} "
            f"with HTTP {entity_response.status_code}"
        )

    write_options = dict(request_options)
    write_options["headers"] = {
        **request_options.get("headers", {}),
        "x-csrf-token": csrf_token,
        "csrf-token": csrf_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    write_options["cookies"] = token_response.cookies

    results = []
    for material, material_rows in grouped.items():
        if material in error_materials:
            results.append({"material": material, "status": "skipped_conversion_errors"})
            continue
        try:
            root_payload = _row_payload(material_rows[0], ROOT_ENTITY_SET, "", validated_map)
            for prop, default_value in DEFAULT_PRODUCT_CREATE_FIELDS.items():
                root_payload.setdefault(prop, default_value)
            root_payload[ROOT_KEY_PROPERTY] = ""
            child_payloads = []
            for navigation, entity in metadata["root_navigations"].items():
                child_payload = _row_payload(material_rows[0], entity, navigation, validated_map)
                if child_payload:
                    if navigation == "to_Description":
                        child_payload.setdefault(
                            "Language", DEFAULT_PRODUCT_DESCRIPTION_LANGUAGE
                        )
                        child_payload[ROOT_KEY_PROPERTY] = ""
                        root_payload[navigation] = {"results": [child_payload]}
                    else:
                        child_payloads.append((navigation, child_payload))
            # Supply-planning data must accompany the plant create request.
            lot_size = material_rows[0].get("DISLS")
            if lot_size not in (None, ""):
                plant = next((payload for nav, payload in child_payloads if nav == "to_Plant"), None)
                if plant is None or not plant.get("Plant"):
                    raise ValueError("Lot sizing requires a mapped Plant for supply planning")
                plant_entity = metadata["root_navigations"].get("to_Plant")
                plant_type = metadata["entity_types"].get(metadata["entity_sets"].get(plant_entity), {})
                if "to_ProductSupplyPlanning" not in plant_type.get("navigations", {}):
                    raise ValueError("Plant supply-planning navigation is absent from S/4 metadata")
                plant["to_ProductSupplyPlanning"] = {
                    "Plant": plant["Plant"], "LotSizingProcedure": str(lot_size).strip(),
                    **{key: plant[key] for key in ("MRPType", "MRPResponsible", "ProcurementType") if key in plant},
                }
            if "to_Description" not in root_payload:
                raise ValueError(
                    "S/4 requires a product description during product creation; map a populated source field to ProductDescription"
                )
            root_type = metadata["entity_types"][metadata["root_type"]]
            missing_required = [
                prop for prop in root_type["required_properties"]
                if root_payload.get(prop) in (None, "")
            ]
            if missing_required:
                raise ValueError(
                    "A_Product payload is missing metadata-required property/properties: "
                    + ", ".join(sorted(missing_required))
                )
        except ValueError as exc:
            results.append({"material": material, "status": "invalid_mapping_or_values", "error": str(exc)})
            continue

        collection_url = f"{root_url}/{ROOT_ENTITY_SET}"
        create_response = session.post(collection_url, json=root_payload, **write_options)
        if not create_response.ok:
            sap_error = _response_error(create_response)
            create_not_allowed = "create operation not allowed" in sap_error.casefold()
            results.append({
                "material": material,
                "status": "create_not_allowed" if create_not_allowed else "product_create_failed",
                "http_status": create_response.status_code,
                "method": "POST",
                "endpoint": f"{PRODUCT_SERVICE_ROOT}/{ROOT_ENTITY_SET}",
                "sap_request_id": create_response.headers.get("x-vcap-request-id"),
                "request_fields": sorted(
                    field for field in root_payload if field not in metadata["root_navigations"]
                ),
                "error": (
                    "S/4 Gateway rejected POST create on A_Product with HTTP "
                    f"{create_response.status_code}. SAP returned this create-operation error; "
                    "the message alone does not distinguish an entity capability, request-shape, "
                    "or backend implementation issue. "
                    f"SAP response: {sap_error}"
                    if create_not_allowed else sap_error
                ),
            })
            continue

        generated_id = _generated_product_id(create_response)
        related_results = []
        if child_payloads and generated_id:
            escaped_id = quote(generated_id.replace("'", "''"), safe="")
            for navigation, child_payload in child_payloads:
                child_payload[ROOT_KEY_PROPERTY] = generated_id
                if navigation == "to_Plant" and "to_ProductSupplyPlanning" in child_payload:
                    child_payload["to_ProductSupplyPlanning"][ROOT_KEY_PROPERTY] = generated_id
                child_url = f"{root_url}/{ROOT_ENTITY_SET}('{escaped_id}')/{navigation}"
                child_response = session.post(child_url, json=child_payload, **write_options)
                related_results.append({
                    "entity": navigation,
                    "status": "created" if child_response.ok else "failed",
                    "http_status": child_response.status_code,
                    "method": "POST",
                    "endpoint": f"{PRODUCT_SERVICE_ROOT}/{ROOT_ENTITY_SET}('{escaped_id}')/{navigation}",
                    "included_deep_entities": ["to_ProductSupplyPlanning"] if "to_ProductSupplyPlanning" in child_payload else [],
                    **({} if child_response.ok else {"error": _response_error(child_response)}),
                })
        elif child_payloads:
            related_results.extend({
                "entity": navigation,
                "status": "failed",
                "error": "SAP created the product but did not return its generated Product key; related data was not sent",
            } for navigation, _ in child_payloads)
        has_related_errors = any(item["status"] == "failed" for item in related_results)
        results.append({
            "source_material": material,
            "material": generated_id or material,
            "product_id": generated_id,
            "root_http_status": create_response.status_code,
            "root_included_deep_entities": ["to_Description"],
            "status": "created_with_errors" if has_related_errors else "created",
            **({"related_results": related_results} if related_results else {}),
        })

    counts = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return {
        "status": "completed",
        "created": counts.get("created", 0),
        "partial": counts.get("created_with_errors", 0),
        "already_exists": counts.get("already_exists_skipped", 0),
        "skipped_with_errors": counts.get("skipped_conversion_errors", 0),
        "failed": sum(count for status, count in counts.items() if status not in {
            "created", "created_with_errors", "already_exists_skipped", "skipped_conversion_errors",
        }),
        "results": results,
    }
