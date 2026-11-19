from rag.memory import ConversationMemory
from rag import summaries


def test_incremental_summary_covers_all_turns_and_agents_are_separate(tmp_path, monkeypatch):
    store = ConversationMemory('sqlite:///' + str(tmp_path / 'summary.sqlite3'))
    for n in range(12):
        store.save_turn('ibp', f'chat-{n}', f'question-{n}', f'answer-{n}')
    store.save_turn('s4', 'other', 's4-only', 's4-response')
    calls = []
    class FakeClient:
        def complete(self, system, user, max_tokens):
            calls.append(user)
            return 'Updated historical summary'
    monkeypatch.setattr(summaries, 'get_llm_client', lambda: FakeClient())
    result = summaries.refresh_summary(store, 'ibp')
    assert result['turn_count'] == 12
    assert len(calls) == 3
    assert all('s4-only' not in call for call in calls)
    assert summaries.refresh_summary(store, 'ibp')['turn_count'] == 12
    assert len(calls) == 3
    store.save_turn('ibp', 'later', 'new question', 'new answer')
    assert summaries.refresh_summary(store, 'ibp')['turn_count'] == 13
    assert len(calls) == 4


def test_summary_rebuilds_if_an_older_turn_arrives_late(tmp_path, monkeypatch):
    store = ConversationMemory('sqlite:///' + str(tmp_path / 'late.sqlite3'))
    store.save_turn('ibp', 'one', 'first', 'first answer')
    class FakeClient:
        def complete(self, system, user, max_tokens):
            return 'Summary of stored turns'
    monkeypatch.setattr(summaries, 'get_llm_client', lambda: FakeClient())
    assert summaries.refresh_summary(store, 'ibp')['turn_count'] == 1
    late = store.save_turn('ibp', 'two', 'late', 'late answer')
    with store.connection() as conn:
        store._execute(conn, 'UPDATE rag_chat_turns SET created_at = ? WHERE turn_id = ?', ('2000-01-01T00:00:00+00:00', late))
    assert summaries.refresh_summary(store, 'ibp')['turn_count'] == 2
