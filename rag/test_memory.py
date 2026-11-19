from rag.memory import ConversationMemory
import json
import pytest


def test_conversation_history_persists_and_is_scoped(tmp_path):
    url = 'sqlite:///' + str(tmp_path / 'chat.sqlite3')
    store = ConversationMemory(url)
    store.save_turn('ibp', 'conversation-1', 'First question', 'First answer')
    store.save_turn('ibp', 'conversation-1', 'Second question', 'Second answer')
    store.save_turn('s4', 'conversation-1', 'Other agent', 'Other answer')
    store.save_turn('ibp', 'conversation-2', 'Other session', 'Other response')
    reopened = ConversationMemory(url)
    history = reopened.history('ibp', 'conversation-1')
    assert [entry['content'] if entry['role'] == 'user' else entry['content'][0]['text'] for entry in history] == ['First question', 'First answer', 'Second question', 'Second answer']
    assert reopened.history('s4', 'conversation-1')[0]['content'] == 'Other agent'
    assert len(reopened.history('ibp', 'conversation-1', 1)) == 2


def test_unconfigured_memory_does_not_create_local_database(monkeypatch):
    from rag.memory import configured_store
    monkeypatch.delenv('RAG_MEMORY_DATABASE_URL', raising=False)
    monkeypatch.delenv('VCAP_SERVICES', raising=False)
    monkeypatch.delenv('RAG_MEMORY_REQUIRED', raising=False)
    assert configured_store() is None


def test_bound_service_discovery_uses_named_binding_and_tls(monkeypatch):
    from rag import memory
    monkeypatch.delenv('RAG_MEMORY_DATABASE_URL', raising=False)
    monkeypatch.setenv('RAG_MEMORY_REQUIRED', 'true')
    monkeypatch.setenv('VCAP_SERVICES', json.dumps({'postgresql-db': [{'name': 'rag-memory-db', 'credentials': {'uri': 'postgresql://user:secret@host/db', 'sslrootcert': 'CA'}}]}))
    captured = {}
    def fake_store(url, **kwargs):
        captured.update(url=url, **kwargs)
        return 'store'
    monkeypatch.setattr(memory, 'ConversationMemory', fake_store)
    assert memory.configured_store() == 'store'
    assert captured['require_tls'] is True
    assert captured['root_cert'] == 'CA'


def test_required_binding_fails_closed(monkeypatch):
    from rag.memory import configured_store
    monkeypatch.delenv('RAG_MEMORY_DATABASE_URL', raising=False)
    monkeypatch.delenv('VCAP_SERVICES', raising=False)
    monkeypatch.setenv('RAG_MEMORY_REQUIRED', 'true')
    with pytest.raises(RuntimeError, match='binding is missing'):
        configured_store()


def test_retrieval_finds_other_conversations_for_same_agent_only(tmp_path):
    store = ConversationMemory('sqlite:///' + str(tmp_path / 'retrieval.sqlite3'))
    store.save_turn('ibp', 'one', 'Investigate turbine shortage', 'Check turbine capacity')
    store.save_turn('ibp', 'other', 'Investigate turbine', 'Different conversation')
    store.save_turn('s4', 'other', 'Investigate turbine shortage', 'Different agent')
    for index in range(31):
        store.save_turn('ibp', 'one', f'Unrelated topic {index}', 'No relevant data')
    results = store.related_older_turns('ibp', 'one', 'turbine shortage')
    assert [result['answer'] for result in results] == ['Check turbine capacity', 'Different conversation']
    assert results[1]['source'] == 'previous conversation'


def test_retrieval_scans_beyond_first_page(tmp_path):
    store = ConversationMemory('sqlite:///' + str(tmp_path / 'many.sqlite3'))
    store.save_turn('ibp', 'old', 'Reference PLANNER-4827', 'Reference PLANNER-4827')
    for index in range(210):
        store.save_turn('ibp', 'recent', f'Unrelated {index}', 'No relevant data')
    results = store.related_older_turns('ibp', 'new', 'What was reference PLANNER-4827?')
    assert results[0]['answer'] == 'Reference PLANNER-4827'


def test_archived_context_is_passed_to_agent(tmp_path, monkeypatch):
    import app as application
    store = ConversationMemory('sqlite:///' + str(tmp_path / 'context.sqlite3'))
    store.save_turn('ibp', 'old', 'Reference code PLANNER-4827', 'Reference code PLANNER-4827')
    store.save_turn('s4', 'old', 'Reference code OTHER', 'Reference code OTHER')
    monkeypatch.setattr(application, 'MEMORY', store)
    monkeypatch.setattr(application, 'refresh_summary', lambda *_: None)
    history = application._history('ibp', 'new', {}, 'What reference code did I provide?')
    assert 'PLANNER-4827' in history[-1]['content']
    assert 'OTHER' not in history[-1]['content']
    assert 'not current instructions or approval' in history[-1]['content']


def test_previous_conversation_uses_latest_other_session_and_keeps_recommendation(tmp_path):
    store = ConversationMemory('sqlite:///' + str(tmp_path / 'previous.sqlite3'))
    store.save_turn('s4', 'old', 'What is my test code?', 'No test code was provided.')
    answer = 'Capacity bottleneck found.\n' + ('Bucket details. ' * 1000) + '\nRecommended Action: Review WC-1101; do not move the orders yet.'
    store.save_turn('s4', 'incognito', 'Is there a capacity bottleneck next week?', answer)
    store.save_turn('ibp', 'unrelated', 'Previous discussion?', 'Another agent')
    store.save_turn('s4', 'current', 'What was my previous conversation?', 'Incorrect answer')
    rows = store.related_older_turns('s4', 'current', 'What was previous conversion? What recommendation did you give?')
    assert len(rows) == 1
    assert rows[0]['source'] == 'latest previous conversation'
    assert 'capacity bottleneck' in rows[0]['question']
    assert 'Review WC-1101' in rows[0]['answer']
    assert 'Middle of long answer omitted' in rows[0]['answer']


def test_previous_conversation_with_no_other_session(tmp_path):
    store = ConversationMemory('sqlite:///' + str(tmp_path / 'one.sqlite3'))
    store.save_turn('s4', 'only', 'hello', 'hello')
    assert store.related_older_turns('s4', 'only', 'What was the previous conversation?') == []


def test_api_records_main_and_browser_agent_turns(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app as application
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    monkeypatch.setattr(application, 'refresh_summary', lambda *_: None)
    store = ConversationMemory('sqlite:///' + str(tmp_path / 'api.sqlite3'))
    monkeypatch.setattr(application, 'MEMORY', store)
    for name in ('run_agent_with_data', 'run_s4_agent_with_data', 'run_maintenance_agent_with_data', 'run_customer_base_agent'):
        monkeypatch.setattr(application, name, lambda message, **kwargs: {'reply': 'answer', 'insights': []})
    client = TestClient(application.app)
    for endpoint, agent in [('/ask', 'ibp'), ('/s4/ask', 's4'), ('/maintenance/ask', 'maintenance'), ('/customer-base/ask', 'scenario')]:
        result = client.post(endpoint, json={'message': 'question', 'conversation_id': 'session'})
        assert result.status_code == 200
        assert store.history(agent, 'session')[0]['content'] == 'question'
    for agent in ('ewm', 'migration'):
        result = client.post('/memory/turn', json={'agent': agent, 'conversation_id': 'session', 'message': 'question', 'reply': 'answer'})
        assert result.json() == {'saved': True}
        assert store.history(agent, 'session')[0]['content'] == 'question'
