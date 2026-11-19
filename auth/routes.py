"""Login, role administration and application-wide API protection."""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from auth.store import AuthStore

COOKIE = 'agent_session'
_attempts = defaultdict(deque)
_attempt_lock = threading.Lock()


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class NewUser(Login):
    role: str = 'user'


class ChangeUser(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


def _allowed_login(key: str) -> bool:
    now = time.monotonic()
    with _attempt_lock:
        # Bound memory usage while cleaning stale buckets.
        for item in list(_attempts):
            if not _attempts[item] or now - _attempts[item][-1] > 300:
                del _attempts[item]
        if len(_attempts) > 10000 and key not in _attempts:
            return False
        bucket = _attempts[key]
        while bucket and now - bucket[0] > 300:
            bucket.popleft()
        if len(bucket) >= 5:
            return False
        bucket.append(now)
        return True


def install_auth(app: FastAPI):
    @app.middleware('http')
    async def protect(request: Request, call_next):
        path = request.url.path
        enabled = os.environ.get('AUTH_ENABLED', 'true').lower() == 'true'
        if not enabled:
            return await call_next(request)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if origin and (urlsplit(origin).scheme not in {'https', 'http'} or urlsplit(origin).netloc.lower() != request.headers.get('host', '').lower()):
                return JSONResponse({'detail': 'Invalid request origin.'}, status_code=403)
        public = path in {'/', '/dashboard', '/health', '/auth/login', '/auth/me', '/auth/logout'} or path.startswith(('/static/', '/landing/'))
        if public:
            return await call_next(request)
        store = getattr(app.state, 'auth', None)
        if not store:
            return JSONResponse({'detail': 'Authentication is unavailable.'}, status_code=503)
        principal = await run_in_threadpool(store.session_user, request.cookies.get(COOKIE))
        if not principal:
            return JSONResponse({'detail': 'Please sign in.'}, status_code=401)
        if (path.startswith('/auth/users') or path.startswith('/auth/feedback')) and principal['role'] != 'admin':
            return JSONResponse({'detail': 'Administrator access required.'}, status_code=403)
        request.state.user = principal
        return await call_next(request)

    @app.post('/auth/login')
    def login(body: Login, request: Request, response: Response):
        store: AuthStore = request.app.state.auth
        key = (request.client.host if request.client else '') + ':' + body.username.strip().lower()
        if not _allowed_login(key):
            raise HTTPException(status_code=429, detail='Too many attempts. Try again later.')
        username = store.authenticate(body.username, body.password)
        if not username:
            raise HTTPException(status_code=401, detail='Invalid username or password.')
        token = store.new_session(username)
        secure = os.environ.get('AUTH_COOKIE_SECURE', 'true' if os.environ.get('VCAP_APPLICATION') else 'false').lower() == 'true'
        response.set_cookie(COOKIE, token, httponly=True, secure=secure, samesite='strict', max_age=28800, path='/')
        return store.session_user(token)

    @app.get('/auth/me')
    def me(request: Request):
        store = getattr(request.app.state, 'auth', None)
        user = store.session_user(request.cookies.get(COOKIE)) if store else None
        if not user:
            raise HTTPException(status_code=401, detail='Please sign in.')
        return user

    @app.post('/auth/logout')
    def logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE)
        if token and getattr(request.app.state, 'auth', None):
            request.app.state.auth.logout(token)
        response.delete_cookie(COOKIE, path='/')
        return {'ok': True}

    @app.get('/auth/users')
    def users(request: Request):
        return {'users': request.app.state.auth.list_users()}

    @app.post('/auth/users')
    def create_user(body: NewUser, request: Request):
        try:
            request.app.state.auth.create_user(body.username, body.password, body.role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {'ok': True}

    @app.patch('/auth/users/{username}')
    def change_user(username: str, body: ChangeUser, request: Request):
        try:
            request.app.state.auth.update_user(username, role=body.role, active=body.active, password=body.password, actor=request.state.user['username'])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {'ok': True}
