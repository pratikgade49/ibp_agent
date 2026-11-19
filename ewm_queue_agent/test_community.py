from unittest.mock import patch

import requests

from ewm_queue_agent import community


def test_search_and_grounded_summary():
    community._cache.clear()
    calls = []
    def fake_liql(query):
        calls.append(query)
        if "MATCHES" in query:
            return [{"id": "123", "subject": "Material missing", "view_href": "/t5/q-a/example/qaq-p/123", "search_snippet": "<b>material</b>"}]
        return [{"body": "Check the storage location extension."}]
    with patch.object(community, "liql", side_effect=fake_liql), patch.object(community, "get_llm_client") as llm:
        llm.return_value.complete.return_value = '{"suggestions":[{"text":"Check storage location extension", "sources":[1]},{"text":"unsupported", "sources":[9]}]}'
        result = community.find_solutions("Material 558 NL01 NAFS does not exist", "M7", "006")
    assert "558" not in calls[0] and "NL01" not in calls[0]
    assert result["suggestions"] == [{"text": "Check storage location extension", "sources": [1]}]
    assert result["sources"][0]["url"].startswith("https://community.sap.com/")


def test_failure_is_not_reported_as_no_matches():
    community._cache.clear()
    with patch.object(community, "liql", side_effect=requests.Timeout()):
        assert community.find_solutions("delivery error", "", "")["status"] == "unavailable"


def test_untrusted_external_links_discarded():
    community._cache.clear()
    with patch.object(community, "liql", return_value=[{"id": "1", "view_href": "https://example.com/"}]):
        assert community.find_solutions("delivery error", "", "")["status"] == "no_matches"


def test_search_without_message():
    assert community.find_solutions("", "", "")["status"] == "no_error"
