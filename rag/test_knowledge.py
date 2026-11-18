from pathlib import Path

from rag.knowledge import retrieve


def test_retrieval_ranks_matching_guidance_and_returns_source(tmp_path: Path):
    (tmp_path / "capacity.md").write_text(
        "# Capacity guidance\n\nInspect PCAPAUSAGE and CAPASUPPLY for production capacity.\n", encoding="utf-8"
    )
    (tmp_path / "forecast.md").write_text("# Forecast\n\nReview forecast anomalies.", encoding="utf-8")
    results = retrieve("PCAPAUSAGE capacity", tmp_path)
    assert results[0]["source"] == "capacity.md"
    assert "PCAPAUSAGE" in results[0]["text"]
    assert results[0]["section"] == 2


def test_no_matches_or_missing_directory_return_no_context(tmp_path: Path):
    (tmp_path / "forecast.md").write_text("Forecast signals", encoding="utf-8")
    assert retrieve("warehouse maintenance", tmp_path) == []
    assert retrieve("forecast", tmp_path / "missing") == []


def test_only_markdown_within_size_limit_is_indexed(tmp_path: Path):
    (tmp_path / "private.txt").write_text("secretkey", encoding="utf-8")
    (tmp_path / "large.md").write_text("secretkey " * 30000, encoding="utf-8")
    assert retrieve("secretkey", tmp_path) == []


def test_agent_receives_reference_data_and_original_question(monkeypatch):
    from agent import graph

    captured = {}
    class FakeGraph:
        def invoke(self, state):
            captured.update(state)
            return {"messages": [{"role": "assistant", "content": [{"type": "text", "text": "Answer"}]}], "insights": []}

    monkeypatch.setattr(graph, "build_graph", lambda: FakeGraph())
    monkeypatch.setattr(graph, "retrieve", lambda query: [{"source": "guide.md", "section": 1, "text": "Guidance"}])
    result = graph.run_agent_with_data("Check capacity")
    assert captured["messages"][-1]["content"] == "Check capacity"
    assert "guide.md" in captured["messages"][-2]["content"]
    assert "untrusted" in captured["messages"][-2]["content"]
    assert result["reply"] == "Answer"
