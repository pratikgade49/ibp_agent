from agent import tools
from agent.tools import _DEFAULT_MASTER_DATA_RULES, _row_checks


def test_zero_pratio_is_reported_as_production_receipt_blocker():
    findings = _row_checks(
        [{"PRDID": "FG-100", "LOCID": "PLANT-A", "PRATIO": 0}],
        "ZJPSOURCEPRODUCTION",
        _DEFAULT_MASTER_DATA_RULES["types"]["SOURCEPRODUCTION"],
        set(),
    )

    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert findings[0]["code"] == "ZERO_PRODUCTION_RATIO"
    assert "set PRATIO to 1" in findings[0]["message"]


def test_positive_pratio_does_not_report_production_ratio_blocker():
    findings = _row_checks(
        [{"PRDID": "FG-100", "LOCID": "PLANT-A", "PRATIO": 1}],
        "ZJPSOURCEPRODUCTION",
        _DEFAULT_MASTER_DATA_RULES["types"]["SOURCEPRODUCTION"],
        set(),
    )

    assert not findings


def test_production_receipt_status_reads_production_key_figure(monkeypatch):
    monkeypatch.setattr(tools, "USE_MOCK_DATA", False)

    def fake_ibp_get(select, filter_):
        assert "PRODUCTION" in select
        assert "PRDID eq 'FG-100'" in filter_
        assert "LOCID eq 'PLANT-A'" in filter_
        return {
            "d": {
                "results": [{
                    "PRDID": "FG-100",
                    "LOCID": "PLANT-A",
                    "SOURCEID": "PDS-01",
                    "PERIODID3_TSTAMP": "/Date(1790812800000)/",
                    "PRODUCTION": "0",
                }]
            }
        }

    monkeypatch.setattr(tools, "_ibp_get", fake_ibp_get)
    result = tools.get_production_receipts_status(
        product="FG-100",
        location="PLANT-A",
        period_start_rel=0,
        period_end_rel=0,
    )

    assert result["key_figure"] == "PRODUCTION"
    assert result["status"] == "no_receipts"
    assert result["total_production"] == 0
    assert result["zero_receipt_rows"][0]["source"] == "PDS-01"


def test_no_production_rows_are_not_reported_as_zero_receipts(monkeypatch):
    monkeypatch.setattr(tools, "USE_MOCK_DATA", False)
    monkeypatch.setattr(tools, "_ibp_get", lambda **_kwargs: {"d": {"results": []}})

    result = tools.get_production_receipts_status(product="FG-100")

    assert result["status"] == "no_data"
    assert result["total_production"] is None