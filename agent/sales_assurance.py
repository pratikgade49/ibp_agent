"""Sales and commercial assurance scenario response."""

from __future__ import annotations


SCENARIO_EVIDENCE = {
    "evidence_type": "sales_assurance",
    "data_status": "Illustrative scenario; not live SAP data",
    "last_updated": "Illustrative scenario snapshot",
    "disruption": {
        "event": "Production line interruption",
        "line": "Filling line 2",
        "plant": "Hamburg plant",
        "work_center": "PKG-L2",
        "started_at": "Oct 14, 08:20",
        "expected_recovery": "Not available",
        "affected_products": ["Citrus concentrate 20L", "Citrus concentrate 5L"],
        "fact_status": "Illustrative details; recovery estimate unavailable",
    },
    "orders": [
        {
            "order": "48001264",
            "customer": "Northstar Beverages",
            "material": "Citrus concentrate 20L",
            "quantity": "1,200 cases",
            "requested_date": "Oct 16, 2026",
            "confirmed_date": "Oct 16, 2026",
            "plant": "Hamburg",
            "value_exposed": "$980,000",
            "risk": "High",
            "reason": "All confirmed quantity is assigned to the interrupted line.",
        },
        {
            "order": "48001281",
            "customer": "Harbor & Field Foods",
            "material": "Citrus concentrate 5L",
            "quantity": "840 cases",
            "requested_date": "Oct 17, 2026",
            "confirmed_date": "Oct 18, 2026",
            "plant": "Hamburg",
            "value_exposed": "$840,000",
            "risk": "High",
            "reason": "Production availability is dependent on the interrupted line.",
        },
        {
            "order": "48001305",
            "customer": "Meridian Drinks Co.",
            "material": "Citrus concentrate 20L",
            "quantity": "620 cases",
            "requested_date": "Oct 19, 2026",
            "confirmed_date": "Oct 20, 2026",
            "plant": "Hamburg",
            "value_exposed": "$580,000",
            "risk": "Medium",
            "reason": "Schedule recovery is unconfirmed; current commitment may be affected.",
        },
    ],
    "order_value_exposed": "$2.4M",
    "alternatives": [
        {
            "option": "A",
            "supply_source": "Hamburg plant after recovery",
            "delivery_outcome": "Current dates at risk",
            "constraint": "Recovery estimate unavailable",
            "freight_impact": "No incremental freight identified",
            "margin_impact": "Not evaluated",
            "recommendation": "Baseline; reassess after recovery estimate",
        },
        {
            "option": "B",
            "supply_source": "Rotterdam plant candidate",
            "delivery_outcome": "Target dates may be achievable",
            "constraint": "Material, inventory, capacity, and transit checks required",
            "freight_impact": "Not evaluated",
            "margin_impact": "Not evaluated",
            "recommendation": "Preferred for feasibility review; not yet validated",
        },
    ],
    "recommendation": {
        "action": "Validate Rotterdam as an alternate source for the highest-risk orders.",
        "rationale": "A second source may protect customer dates, but no feasibility checks are confirmed.",
        "expected_impact": "Delivery dates, incremental freight, and margin remain unverified.",
        "approval_required": "Sales manager and supply planning",
        "approval_status": "Review pending",
        "sap_update_status": "No SAP sales document update submitted",
    },
}


def run_sales_assurance_with_data(
    user_message: str, history: list[dict] | None = None
) -> dict:
    """Return a transparent illustrative scenario for the assurance workflow."""
    normalized_message = " ".join(user_message.casefold().split())
    evidence = {
        **SCENARIO_EVIDENCE,
        "disruption": dict(SCENARIO_EVIDENCE["disruption"]),
        "orders": [dict(order) for order in SCENARIO_EVIDENCE["orders"]],
        "alternatives": [dict(option) for option in SCENARIO_EVIDENCE["alternatives"]],
        "recommendation": dict(SCENARIO_EVIDENCE["recommendation"]),
    }

    confirms_approval = normalized_message in {
        "approve",
        "approve recommendation",
        "yes",
        "yes, approve",
        "proceed",
    }
    if confirms_approval:
        evidence["recommendation"]["approval_status"] = "Approval recorded for review"
        evidence["recommendation"]["sap_update_status"] = (
            ""
        )
        reply = (
            "Approval has been recorded for review. No SAP sales document or "
            "delivery commitment was changed. Validate the alternate plant's "
            "material, inventory, capacity, and transit constraints before any update."
        )
    elif "reject" in normalized_message:
        evidence["recommendation"]["approval_status"] = "Recommendation rejected"
        reply = (
            "The recommendation has been marked as rejected for this review. "
            "No SAP sales document or delivery commitment was changed."
        )
    elif "further analysis" in normalized_message:
        evidence["recommendation"]["approval_status"] = "Further analysis requested"
        reply = (
            "Further analysis has been requested. Confirm alternate-plant "
            "material compatibility, inventory, capacity, transit time, and "
            "commercial impact before revising any delivery commitment."
        )
    elif any(term in normalized_message for term in ("margin", "compare", "alternative", "option")):
        reply = (
            "The Rotterdam plant is a candidate for feasibility review, not a "
            "confirmed supply option. Material compatibility, inventory, "
            "capacity, and transit time still need validation. Freight and "
            "margin effects are not available, so no margin improvement is claimed."
        )
    elif any(term in normalized_message for term in ("approve", "approval", "review")):
        reply = (
            "The proposed action is to validate Rotterdam as an alternate source "
            "for the highest-risk orders. Approval is for review only; it does not "
            "submit an SAP change. Sales management and supply planning should "
            "confirm feasibility and commercial impact first."
        )
    else:
        reply = (
            "Three illustrative sales orders are exposed to delivery risk, with "
            "$2.4M in order value exposed, not lost revenue. Rotterdam plant is a "
            "candidate for feasibility review; its inventory, capacity, material "
            "compatibility, and transit time are not yet confirmed."
        )

    return {
        "reply": reply,
        "insights": [{"tool": "sales_assurance_scenario", "data": evidence}],
    }