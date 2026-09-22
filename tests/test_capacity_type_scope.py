import agent.tools as t


def test_all_capacity_does_not_duplicate_same_row_across_types(monkeypatch):
    monkeypatch.setattr(t, "USE_MOCK_DATA", False)

    def fake_ibp_get(select: str, filter_: str):
        return {
            "d": {
                "results": [
                    {
                        "RESID": "TestMachine01",
                        "LOCID": "TestPlant01",
                        "PRDID": "FG-100",
                        "PCAPADEMAND": "421.8",
                        "PCAPAUSAGE": "421.8",
                        "CAPASUPPLY": "2000",
                        "PCAPACONSUMPTION": "1.0",
                        "UTILIZATIONPCT": "21.1",
                        "PERIODID3_TSTAMP": "2026-09-01T00:00:00",
                    }
                ]
            }
        }

    monkeypatch.setattr(t, "_ibp_get", fake_ibp_get)

    result = t.analyze_capacity_bottlenecks(resource_type="all", period_start_rel=1, period_end_rel=3)

    types = {item["resource_type"] for item in result["results"]}
    assert types == {"production"}
    assert all(item["resource"] == "TestMachine01" for item in result["results"])


def test_all_capacity_total_rows_are_summed_across_resource_types(monkeypatch):
    monkeypatch.setattr(t, "USE_MOCK_DATA", True)

    result = t.analyze_capacity_bottlenecks(resource_type="all", period_start_rel=0, period_end_rel=3)

    assert result["total_rows_evaluated"] == 2
    assert result["resource_period_count"] == 2
    assert result["analysis_status"] == "complete"
