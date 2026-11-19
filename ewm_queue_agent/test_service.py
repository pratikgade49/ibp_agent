from unittest.mock import Mock, patch
from datetime import date

import pytest

from ewm_queue_agent.service import diagnose, fetch_queues
from ewm_queue_agent.interpret import interpret_request


def response(rows, next_link=None):
    data = {"results": rows}
    if next_link:
        data["__next"] = next_link
    result = Mock(ok=True)
    result.json.return_value = {"d": data}
    return result


def test_sample_error_and_direction():
    item = diagnose({"Mtypetext": "Inbound", "Qstate": "SYSFAIL", "Errmess": "Delivery type cannot be determined", "Wqname": "PREVIOUS"})
    assert item["transaction"] == "SMQ2"
    assert any("delivery type" in step.lower() for step in item["guidance"])
    assert any("PREVIOUS" in step for step in item["guidance"])


@patch("ewm_queue_agent.service._s4_request_config", return_value=("https://sap.example", {"timeout": 5}))
@patch("ewm_queue_agent.service.requests.get")
def test_paginated_warehouse_lookup(get, config):
    get.side_effect = [response([{"Qname": "A"}], "?page=2"), response([{"Qname": "B"}])]
    result = fetch_queues("5070")
    assert [row["Qname"] for row in result["queues"]] == ["A", "B"]
    assert get.call_args_list[0].kwargs["params"]["$filter"] == "Lgnum eq '5070'"
    assert result["truncated"] is False


@patch("ewm_queue_agent.service._s4_request_config", return_value=("https://sap.example", {"timeout": 5}))
@patch("ewm_queue_agent.service.requests.get")
def test_rejects_cross_origin_next_link(get, config):
    get.return_value = response([], "https://other.example/page")
    with pytest.raises(RuntimeError, match="unsafe"):
        fetch_queues()
    assert get.call_count == 1


@pytest.mark.parametrize("phrase,start,end", [
    ("today", "2026-11-18", "2026-11-18"),
    ("last 3 days", "2026-11-16", "2026-11-18"),
    ("last week", "2026-11-09", "2026-11-15"),
    ("last month", "2026-10-01", "2026-10-31"),
    ("show queues for warehouse 5070 from May 2026", "2026-05-01", "2026-11-18"),
    ("since Sep 2026", "2026-09-01", "2026-11-18"),
    ("from May 2026 to June 2026", "2026-05-01", "2026-06-30"),
    ("from 2026-03-01 to 2026-03-08", "2026-03-01", "2026-03-08"),
    ("since 2026-03-08", "2026-03-08", None),
])
def test_relative_and_absolute_dates(phrase, start, end):
    result = interpret_request(phrase, "5070", date(2026, 11, 18))
    assert result == {"warehouse": "5070", "date_from": start, "date_to": end}


@patch("ewm_queue_agent.service._s4_request_config", return_value=("https://sap.example", {"timeout": 5}))
@patch("ewm_queue_agent.service.requests.get")
def test_date_filters(get, config):
    get.return_value = response([])
    fetch_queues("5070", date(2026, 3, 8), date(2026, 3, 8))
    assert get.call_args.kwargs["params"]["$filter"] == "Lgnum eq '5070' and Creadate eq datetime'2026-03-08T00:00:00'"
    fetch_queues("5070", date(2026, 3, 1), date(2026, 3, 8))
    assert get.call_args.kwargs["params"]["$filter"] == "Lgnum eq '5070' and Creadate ge datetime'2026-03-01T00:00:00' and Creadate le datetime'2026-03-08T23:59:59'"
    fetch_queues("5070", None, date(2026, 1, 27))
    assert get.call_args.kwargs["params"]["$filter"] == "Lgnum eq '5070' and Creadate le datetime'2026-01-27T23:59:59'"
    fetch_queues("5070", date(2026, 1, 27), date(2026, 3, 8))
    assert get.call_args.kwargs["params"]["$filter"] == "Lgnum eq '5070' and Creadate ge datetime'2026-01-27T00:00:00' and Creadate le datetime'2026-03-08T23:59:59'"


@patch("ewm_queue_agent.service._s4_request_config", return_value=("https://sap.example", {"timeout": 5}))
@patch("ewm_queue_agent.service.requests.get")
def test_range_collects_sap_filtered_pages(get, config):
    get.side_effect = [response([{"Qname": "first", "Creadate": "/Date(1772928000000)/"}], "?page=2"), response([{"Qname": "second", "Creadate": "2026-03-07T00:00:00"}])]
    result = fetch_queues("5070", date(2026, 3, 1), date(2026, 3, 8))
    assert [row["Qname"] for row in result["queues"]] == ["first", "second"]
    assert not result["truncated"]


@patch("ewm_queue_agent.service._s4_request_config", return_value=("https://sap.example", {"timeout": 5}))
@patch("ewm_queue_agent.service.requests.get")
def test_truncation_reflects_sap_rows(get, config):
    get.return_value = response([{"Creadate": "2026-04-01T00:00:00"}] * 1001)
    result = fetch_queues("5070", date(2026, 3, 1), date(2026, 3, 8))
    assert result["count"] == 1000
    assert result["truncated"] is True


def test_reversed_date_range_rejected():
    with pytest.raises(ValueError, match="start date"):
        interpret_request("2026-03-08 to 2026-03-01", "5070", date(2026, 11, 18))
