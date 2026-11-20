from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.routes import install_auth
from auth.store import AuthStore
from rag.memory import ConversationMemory


def test_authentication_roles_and_session_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    monkeypatch.setenv('AUTH_COOKIE_SECURE', 'false')
    store = AuthStore(ConversationMemory('sqlite:///' + str(tmp_path / 'auth.sqlite3')))
    store.bootstrap('admin', 'correct horse battery staple')
    store.bootstrap('ignored', 'ignored password 123')
    assert [u['username'] for u in store.list_users()] == ['admin']
    app = FastAPI()
    app.state.auth = store
    install_auth(app)
    @app.post('/ask')
    def ask(): return {'ok': True}
    admin, user = TestClient(app), TestClient(app)
    assert admin.post('/ask').status_code == 401
    assert admin.post('/auth/login', json={'username': 'admin', 'password': 'wrong'}).status_code == 401
    login = admin.post('/auth/login', json={'username': 'admin', 'password': 'correct horse battery staple'})
    assert login.json() == {'username': 'admin', 'role': 'admin'}
    assert 'httponly' in login.headers['set-cookie'].lower()
    assert admin.post('/auth/users', json={'username': 'planner', 'password': 'another strong password', 'role': 'user'}).status_code == 200
    assert user.post('/auth/login', json={'username': 'planner', 'password': 'another strong password'}).status_code == 200
    assert user.post('/ask').status_code == 200
    assert user.get('/auth/users').status_code == 403
    assert user.post('/ask', headers={'origin': 'https://attacker.example'}).status_code == 403
    assert admin.patch('/auth/users/admin', json={'active': False}).status_code == 400
    assert admin.patch('/auth/users/planner', json={'active': False}).status_code == 200
    assert user.post('/ask').status_code == 401
    assert admin.post('/auth/logout').status_code == 200
    assert admin.post('/ask').status_code == 401


def test_bootstrap_requires_valid_credentials_when_empty(tmp_path):
    import pytest
    store = AuthStore(ConversationMemory('sqlite:///' + str(tmp_path / 'empty.sqlite3')))
    with pytest.raises(ValueError):
        store.bootstrap('', '')


def test_real_app_initializes_admin_protects_agents_and_saves_feedback(tmp_path, monkeypatch):
    import app as application
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    monkeypatch.setenv('AUTH_COOKIE_SECURE', 'false')
    monkeypatch.setenv('AUTH_ADMIN_USERNAME', 'bootstrap')
    monkeypatch.setenv('AUTH_ADMIN_PASSWORD', 'a strong initial password')
    monkeypatch.setenv('RAG_MEMORY_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'app.sqlite3'))
    monkeypatch.setattr(application, 'refresh_summary', lambda *_, **kwargs: None)
    monkeypatch.setattr(application, 'run_agent_with_data', lambda message, history: {'reply': 'answer', 'insights': []})
    with TestClient(application.app) as client:
        assert client.post('/ask', json={'message': 'hello'}).status_code == 401
        assert client.post('/auth/login', json={'username': 'bootstrap', 'password': 'a strong initial password'}).status_code == 200
        turn = client.post('/ask', json={'message': 'hello'}).json()['memory_turn_id']
        assert client.post('/memory/feedback', json={'turn_id': turn, 'rating': 'unhelpful', 'correction': 'Please use the correct date.'}).json() == {'saved': True}
        assert client.get('/auth/feedback').json()['feedback'][0]['correction'] == 'Please use the correct date.'


def test_two_signed_in_users_never_share_agent_context(tmp_path, monkeypatch):
    import app as application
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    monkeypatch.setenv('AUTH_COOKIE_SECURE', 'false')
    monkeypatch.setenv('AUTH_ADMIN_USERNAME', 'alice')
    monkeypatch.setenv('AUTH_ADMIN_PASSWORD', 'alice secure password 123')
    monkeypatch.setenv('RAG_MEMORY_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'users.sqlite3'))
    monkeypatch.setattr(application, 'refresh_summary', lambda *_, **kwargs: None)
    observed = []
    def answer(message, history):
        observed.append(history)
        return {'reply': 'answer', 'insights': []}
    monkeypatch.setattr(application, 'run_agent_with_data', answer)
    with TestClient(application.app) as alice:
        assert alice.post('/auth/login', json={'username': 'alice', 'password': 'alice secure password 123'}).status_code == 200
        assert alice.post('/auth/users', json={'username': 'bob', 'password': 'bob secure password 123', 'role': 'user'}).status_code == 200
        first = alice.post('/ask', json={'message': 'secret reference ALICE739', 'conversation_id': 'same-id'}).json()
        with TestClient(application.app) as bob:
            assert bob.post('/auth/login', json={'username': 'bob', 'password': 'bob secure password 123'}).status_code == 200
            assert bob.post('/ask', json={'message': 'reference question', 'conversation_id': 'same-id'}).status_code == 200
            assert 'ALICE739' not in str(observed[-1])
            assert bob.post('/memory/feedback', json={'turn_id': first['memory_turn_id'], 'rating': 'helpful'}).status_code == 400
        assert alice.post('/ask', json={'message': 'reference question', 'conversation_id': 'new-id'}).status_code == 200
        assert 'ALICE739' in str(observed[-1])


def test_recorded_summary_is_supplied_for_a_normal_question(tmp_path, monkeypatch):
    import app as application
    from rag.memory import ConversationMemory
    store = ConversationMemory('sqlite:///' + str(tmp_path / 'proactive.sqlite3'))
    store.save_turn('s4', 'previous', 'I prefer hours in capacity reports', 'I will use hours.', username='alice')
    monkeypatch.setattr(application, 'MEMORY', store)
    monkeypatch.setattr(application, 'refresh_summary', lambda _store, _agent, **kwargs: {'summary': 'User prefers hours in capacity reports.', 'turn_count': 1})
    history = application._history('s4', 'new', {}, 'Are there any bottlenecks next week?', username='alice')
    assert 'prefers hours' in str(history)
    assert 'automatically supplied' in str(history)
