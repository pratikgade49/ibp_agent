"""Interpret warehouse and creation dates without silently dropping date requests."""
import json
import re
import calendar
from datetime import date, timedelta

from agent.llm import get_llm_client


def interpret_request(message: str, previous_warehouse: str, today: date) -> dict:
    text = message.strip()
    warehouse_match = re.search(r"\bwarehouse(?:\s+(?:number|no\.?))?(?:\s+is)?\s*[:#]?\s+([a-z0-9]{1,4})\b", text, re.I)
    warehouse = warehouse_match.group(1) if warehouse_match else previous_warehouse
    if re.fullmatch(r"[A-Za-z0-9]{1,4}", text):
        warehouse = text
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    lower = text.lower()
    start = end = None
    try:
        month_from = re.search(r"\b(?:from|since)\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+(\d{4})\b", lower)
        if month_from:
            month_name = month_from.group(1)[:3].title()
            month = list(calendar.month_abbr).index(month_name)
            start, end = date(int(month_from.group(2)), month, 1), today
            tail = lower[month_from.end():]
            if re.search(r"\b(?:to|through|until)\b", tail):
                end_month = re.search(r"\b(?:to|through|until)\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+(\d{4})\b", tail)
                if end_month:
                    year = int(end_month.group(2))
                    end_num = list(calendar.month_abbr).index(end_month.group(1)[:3].title())
                    end = date(year, end_num, calendar.monthrange(year, end_num)[1])
                elif dates:
                    end = date.fromisoformat(dates[-1])
                elif not re.search(r"\b(?:to|through|until)\s+today\b", tail):
                    raise ValueError("Please specify the end date in YYYY-MM-DD format or as a month and year.")
        elif dates:
            if len(dates) > 2:
                raise ValueError("Please provide one date or a start and end date.")
            try:
                start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
            except ValueError as exc:
                raise ValueError("Please provide a valid date in YYYY-MM-DD format.") from exc
            if len(dates) == 1 and re.search(r"\b(?:since|from|on or after)\b", lower):
                end = None
            elif len(dates) == 1 and re.search(r"\b(?:until|through|up to|on or before)\b", lower):
                start = None
            elif len(dates) == 1 and re.search(r"\bbefore\b", lower):
                start, end = None, end - timedelta(days=1)
            elif len(dates) == 1 and re.search(r"\bafter\b", lower):
                start, end = start + timedelta(days=1), None
        elif re.search(r"\btoday\b", lower):
            start = end = today
        elif re.search(r"\byesterday\b", lower):
            start = end = today - timedelta(days=1)
        elif re.search(r"\blast week\b", lower):
            end = today - timedelta(days=today.weekday() + 1)
            start = end - timedelta(days=6)
        elif re.search(r"\blast month\b", lower):
            end = today.replace(day=1) - timedelta(days=1)
            start = end.replace(day=1)
        elif match := re.search(r"\b(?:last|past)\s+(\d+)\s+days?\b", lower):
            days = int(match.group(1))
            if not 1 <= days <= 36500:
                raise ValueError("Please provide a valid number of days.")
            start, end = today - timedelta(days=days - 1), today
        else:
            # An LLM handles other natural language dates and asks for clarification
            # when a phrase is ambiguous. Its output is validated before use.
            simple = bool(re.fullmatch(r"[A-Za-z0-9]{1,4}", text)) or bool(warehouse_match and re.fullmatch(r"(?:show\s+)?(?:queues?\s+(?:for\s+)?)?warehouse(?:\s+(?:number|no\.?))?(?:\s+is)?\s*[:#]?\s+[a-z0-9]{1,4}[.!]?", text, re.I))
            if not simple:
                try:
                    raw = get_llm_client().complete(
                        "Extract an EWM queue query. Return JSON only: {\"warehouse\": string or null, \"date_from\": YYYY-MM-DD or null, \"date_to\": YYYY-MM-DD or null, \"clarification\": string or null}. Dates are inclusive. Never invent a date or warehouse. If a date expression is ambiguous or cannot be interpreted, set clarification. If no date is requested, both dates are null. 'From May 2026' or 'since May 2026' means May 1 2026 through the supplied current date, whereas 'in May 2026' means the calendar month. Last week means previous Monday through Sunday; last N days includes today. The user's local current date is provided. For one date use it for both dates. Open-ended ranges may have one null endpoint.",
                        f"Current date: {today.isoformat()}\nPreviously selected warehouse: {previous_warehouse or 'none'}\nUser request: {text}", max_tokens=350)
                    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
                    parsed = json.loads(raw)
                    if parsed.get("clarification"):
                        raise ValueError(str(parsed["clarification"])[:300])
                    warehouse = parsed.get("warehouse") or warehouse
                    start = date.fromisoformat(parsed["date_from"]) if parsed.get("date_from") else None
                    end = date.fromisoformat(parsed["date_to"]) if parsed.get("date_to") else None
                except ValueError:
                    raise
                except Exception as exc:
                    raise ValueError("I could not interpret that date. Please use YYYY-MM-DD, a range, today, yesterday, last week, or last N days.") from exc
    except (OverflowError, TypeError) as exc:
        raise ValueError("Please provide a valid creation date or date range.") from exc
    if not warehouse or not isinstance(warehouse, str) or not re.fullmatch(r"[A-Za-z0-9]{1,4}", warehouse):
        raise ValueError("Please provide a warehouse number of up to four letters or digits.")
    if start and end and start > end:
        raise ValueError("The start date must be on or before the end date.")
    return {"warehouse": warehouse, "date_from": start.isoformat() if start else None, "date_to": end.isoformat() if end else None}
