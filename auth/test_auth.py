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
    monkeypatch.setattr(application, 'refresh_summary', lambda *_: None)
    monkeypatch.setattr(application, 'run_agent_with_data', lambda message, history: {'reply': 'answer', 'insights': []})
    with TestClient(application.app) as client:
        assert client.post('/ask', json={'message': 'hello'}).status_code == 401
        assert client.post('/auth/login', json={'username': 'bootstrap', 'password': 'a strong initial password'}).status_code == 200
        turn = client.post('/ask', json={'message': 'hello'}).json()['memory_turn_id']
        assert client.post('/memory/feedback', json={'turn_id': turn, 'rating': 'unhelpful', 'correction': 'Please use the correct date.'}).json() == {'saved': True}
        assert client.get('/auth/feedback').json()['feedback'][0]['correction'] == 'Please use the correct date.'
