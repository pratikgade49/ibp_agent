"""Database-backed account and session management for the agent application."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone


def _now():
    return datetime.now(timezone.utc)


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8, p=3, dklen=32, maxmem=64 * 1024 * 1024)
    return 'scrypt$' + salt.hex() + '$' + digest.hex()


def _verify(password: str, encoded: str) -> bool:
    try:
        scheme, salt, digest = encoded.split('$')
        return scheme == 'scrypt' and hmac.compare_digest(_password_hash(password, bytes.fromhex(salt)), encoded)
    except (ValueError, TypeError):
        return False


def validate_user(username: str, password: str, role: str):
    import re
    username = username.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9._@-]{2,127}', username):
        raise ValueError('Username must be 3–128 letters, numbers or . _ @ - characters.')
    if not 12 <= len(password) <= 256:
        raise ValueError('Password must contain 12–256 characters.')
    if role not in {'admin', 'user'}:
        raise ValueError('Role must be admin or user.')
    return username


class AuthStore:
    def __init__(self, memory):
        self.db = memory
        with self.db.connection() as conn:
            self.db._execute(conn, '''CREATE TABLE IF NOT EXISTS auth_users (
                username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL,
                active INTEGER NOT NULL, created_at TEXT NOT NULL)''')
            self.db._execute(conn, '''CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY, username TEXT NOT NULL,
                expires_at TEXT NOT NULL, FOREIGN KEY (username) REFERENCES auth_users(username))''')
            self.db._execute(conn, 'CREATE INDEX IF NOT EXISTS auth_sessions_user_idx ON auth_sessions(username)')

    def bootstrap(self, username: str, password: str):
        with self.db.connection() as conn:
            count = self.db._execute(conn, 'SELECT COUNT(*) FROM auth_users').fetchone()[0]
            if count:
                return
            username = validate_user(username, password, 'admin')
            self.db._execute(conn, 'INSERT INTO auth_users (username, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (username) DO NOTHING', (username, _password_hash(password), 'admin', 1, _now().isoformat()))

    def authenticate(self, username: str, password: str) -> str | None:
        with self.db.connection() as conn:
            row = self.db._execute(conn, 'SELECT password_hash, active FROM auth_users WHERE username = ?', (username.strip().lower(),)).fetchone()
        # The dummy hash makes unknown usernames take similar work to real users.
        encoded = row[0] if row else 'scrypt$00000000000000000000000000000000$' + '00' * 32
        valid = _verify(password, encoded)
        return username.strip().lower() if row and row[1] and valid else None

    def new_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.db.connection() as conn:
            self.db._execute(conn, 'DELETE FROM auth_sessions WHERE expires_at <= ?', (_now().isoformat(),))
            self.db._execute(conn, 'INSERT INTO auth_sessions (token_hash, username, expires_at) VALUES (?, ?, ?)', (digest, username, (_now() + timedelta(hours=8)).isoformat()))
        return token

    def session_user(self, token: str | None) -> dict | None:
        if not token or len(token) > 256:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.db.connection() as conn:
            row = self.db._execute(conn, 'SELECT u.username, u.role FROM auth_sessions s JOIN auth_users u ON s.username = u.username WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1', (digest, _now().isoformat())).fetchone()
        return {'username': row[0], 'role': row[1]} if row else None

    def logout(self, token: str):
        with self.db.connection() as conn:
            self.db._execute(conn, 'DELETE FROM auth_sessions WHERE token_hash = ?', (hashlib.sha256(token.encode()).hexdigest(),))

    def list_users(self):
        with self.db.connection() as conn:
            rows = self.db._execute(conn, 'SELECT username, role, active, created_at FROM auth_users ORDER BY username').fetchall()
        return [{'username': r[0], 'role': r[1], 'active': bool(r[2]), 'created_at': r[3]} for r in rows]

    def create_user(self, username, password, role):
        username = validate_user(username, password, role)
        with self.db.connection() as conn:
            if self.db._execute(conn, 'SELECT username FROM auth_users WHERE username = ?', (username,)).fetchone():
                raise ValueError('User already exists.')
            self.db._execute(conn, 'INSERT INTO auth_users (username, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?)', (username, _password_hash(password), role, 1, _now().isoformat()))

    def update_user(self, username, *, role=None, active=None, password=None, actor=None):
        username = username.strip().lower()
        with self.db.connection() as conn:
            row = self.db._execute(conn, 'SELECT role, active FROM auth_users WHERE username = ?', (username,)).fetchone()
            if not row:
                raise ValueError('User not found.')
            new_role = role if role is not None else row[0]
            if new_role not in {'admin', 'user'}:
                raise ValueError('Role must be admin or user.')
            if username == actor and (new_role != 'admin' or active is False):
                raise ValueError('You cannot disable or remove your own administrator role.')
            if password is not None:
                validate_user(username, password, new_role)
                self.db._execute(conn, 'UPDATE auth_users SET password_hash = ? WHERE username = ?', (_password_hash(password), username))
            self.db._execute(conn, 'UPDATE auth_users SET role = ?, active = ? WHERE username = ?', (new_role, int(active) if active is not None else row[1], username))
            self.db._execute(conn, 'DELETE FROM auth_sessions WHERE username = ?', (username,))
