"""Conversation storage for local development and PostgreSQL-backed deployments."""
from __future__ import annotations

import os
import json
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4


class ConversationMemory:
    def __init__(self, url: str, *, root_cert: str | None = None, require_tls: bool = False):
        self.url = url
        self.root_cert = root_cert
        self.require_tls = require_tls
        self.postgres = url.startswith(("postgres://", "postgresql://"))
        if not self.postgres and not url.startswith("sqlite:///"):
            raise ValueError("RAG_MEMORY_DATABASE_URL must use postgresql:// or sqlite:///")
        if self.postgres:
            import psycopg  # type: ignore
        self._initialize()

    @contextmanager
    def connection(self):
        if self.postgres:
            import psycopg
            options = {"connect_timeout": 10}
            if self.require_tls:
                options["sslmode"] = "verify-full"
            cert_path = None
            try:
                if self.root_cert:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as cert_file:
                        cert_path = cert_file.name
                        cert_file.write(self.root_cert)
                    options["sslrootcert"] = cert_path
                conn = psycopg.connect(self.url, **options)
            finally:
                if cert_path:
                    os.unlink(cert_path)
        else:
            conn = sqlite3.connect(self.url[len("sqlite:///"):], timeout=30)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _execute(self, connection, query, args=()):
        if self.postgres:
            query = query.replace("?", "%s")
        return connection.execute(query, args)

    def _initialize(self):
        with self.connection() as conn:
            self._execute(conn, """CREATE TABLE IF NOT EXISTS rag_chat_turns (
                turn_id TEXT PRIMARY KEY, agent TEXT NOT NULL, conversation_id TEXT NOT NULL,
                created_at TEXT NOT NULL, user_text TEXT NOT NULL, assistant_text TEXT NOT NULL
            )""")
            self._execute(conn, "CREATE INDEX IF NOT EXISTS rag_chat_conversation_idx ON rag_chat_turns (agent, conversation_id, created_at, turn_id)")
            self._execute(conn, "CREATE INDEX IF NOT EXISTS rag_chat_agent_idx ON rag_chat_turns (agent, created_at, turn_id)")

    def save_turn(self, agent: str, conversation_id: str, question: str, answer: str):
        with self.connection() as conn:
            self._execute(conn, "INSERT INTO rag_chat_turns (turn_id, agent, conversation_id, created_at, user_text, assistant_text) VALUES (?, ?, ?, ?, ?, ?)",
                          (str(uuid4()), agent, conversation_id, datetime.now(timezone.utc).isoformat(timespec="microseconds"), question, answer))

    def history(self, agent: str, conversation_id: str, limit: int = 30) -> list[dict]:
        with self.connection() as conn:
            rows = self._execute(conn, "SELECT user_text, assistant_text FROM rag_chat_turns WHERE agent = ? AND conversation_id = ? ORDER BY created_at DESC, turn_id DESC LIMIT ?", (agent, conversation_id, limit)).fetchall()
        messages = []
        for question, answer in reversed(rows):
            messages.extend([{"role": "user", "content": question}, {"role": "assistant", "content": [{"type": "text", "text": answer}]}])
        return messages

    def related_older_turns(self, agent: str, conversation_id: str, question: str) -> list[dict]:
        """Search this agent's archived conversations, excluding recent current turns."""
        # An explicit request for the previous conversation is about recency,
        # not keyword similarity. "conversion" is a common typo for conversation.
        if re.search(r'\b(?:previous|prior|last|earlier)\s+(?:conversation|conversion|chat|discussion)\b', question, re.I):
            with self.connection() as conn:
                last = self._execute(conn, 'SELECT conversation_id FROM rag_chat_turns WHERE agent = ? AND conversation_id <> ? ORDER BY created_at DESC, turn_id DESC LIMIT 1', (agent, conversation_id)).fetchone()
                if not last:
                    return []
                rows = self._execute(conn, 'SELECT created_at, user_text, assistant_text FROM rag_chat_turns WHERE agent = ? AND conversation_id = ? ORDER BY created_at DESC, turn_id DESC LIMIT 4', (agent, last[0])).fetchall()
            def excerpt(text: str) -> str:
                if len(text) <= 10000:
                    return text
                return text[:4500] + '\n[Middle of long answer omitted]\n' + text[-5500:]
            return [{'date': created, 'source': 'latest previous conversation', 'question': user[:2000], 'answer': excerpt(answer)} for created, user, answer in reversed(rows)]
        stop = {'what', 'when', 'where', 'which', 'about', 'with', 'from', 'show', 'please', 'have', 'that', 'this', 'your', 'the', 'and', 'for', 'are', 'earlier', 'conversation', 'previous', 'previously', 'agent'}
        words = {w for w in re.findall(r'[a-z0-9]+', question.lower()) if len(w) >= 3 and w not in stop}
        if not words:
            return []
        ranked = []
        with self.connection() as conn:
            current = self._execute(conn, 'SELECT turn_id FROM rag_chat_turns WHERE agent = ? AND conversation_id = ? ORDER BY created_at DESC, turn_id DESC LIMIT ?', (agent, conversation_id, 30)).fetchall()
            recent_ids = {row[0] for row in current}
            cursor = self._execute(conn, 'SELECT turn_id, conversation_id, created_at, user_text, assistant_text FROM rag_chat_turns WHERE agent = ? ORDER BY created_at DESC, turn_id DESC', (agent,))
            while rows := cursor.fetchmany(200):
                for turn_id, previous_conversation, created, user, assistant in rows:
                    if turn_id in recent_ids:
                        continue
                    matches = words.intersection(re.findall(r'[a-z0-9]+', (user + ' ' + assistant).lower()))
                    if matches:
                        ranked.append((len(matches), created, previous_conversation, user[:1000], assistant[:1500]))
                ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
                del ranked[2:]
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [{'date': created, 'source': 'previous conversation' if previous != conversation_id else 'earlier in this conversation', 'question': user, 'answer': assistant} for _, created, previous, user, assistant in ranked[:2]]


def configured_store() -> ConversationMemory | None:
    url = os.environ.get("RAG_MEMORY_DATABASE_URL", "").strip()
    if url:
        return ConversationMemory(url)
    required = os.environ.get("RAG_MEMORY_REQUIRED", "").lower() == "true"
    name = os.environ.get("RAG_MEMORY_SERVICE_NAME", "rag-memory-db")
    raw = os.environ.get("VCAP_SERVICES", "")
    if raw:
        try:
            services = json.loads(raw)
            matches = [service for group in services.values() for service in group if service.get("name") == name]
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError("Invalid VCAP_SERVICES configuration") from exc
        if len(matches) > 1:
            raise RuntimeError("Multiple RAG memory service bindings found")
        if matches:
            creds = matches[0].get("credentials", {})
            uri = creds.get("uri", "")
            if not isinstance(uri, str) or not uri.startswith(("postgres://", "postgresql://")):
                raise RuntimeError("RAG memory binding does not contain a PostgreSQL URI")
            return ConversationMemory(uri, root_cert=creds.get("sslrootcert") or None, require_tls=True)
    if required:
        raise RuntimeError("Required RAG memory database binding is missing")
    return None
