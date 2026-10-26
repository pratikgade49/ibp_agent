"""SQLite reference run store with per-version active-run exclusion."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone


class ActiveRunExists(RuntimeError):
    """Raised when a version already has a run awaiting completion."""


class RunNotFound(RuntimeError):
    """Raised when the run is absent or not visible to the requester."""


class RunStore:
    def __init__(self, database_path: str):
        self.database_path = database_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS customer_base_runs (
                    run_id TEXT PRIMARY KEY,
                    planning_version_id TEXT NOT NULL DEFAULT '',
                    scenario_id TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT
                )"""
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(customer_base_runs)")
            }
            if "version_id" in columns and "scenario_id" not in columns:
                connection.execute(
                    "ALTER TABLE customer_base_runs RENAME COLUMN version_id TO scenario_id"
                )
                columns.remove("version_id")
                columns.add("scenario_id")
            if "planning_version_id" not in columns:
                connection.execute(
                    "ALTER TABLE customer_base_runs ADD COLUMN planning_version_id TEXT NOT NULL DEFAULT ''"
                )
            connection.execute("DROP INDEX IF EXISTS one_active_run_per_version_v2")
            connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_version_scenario_v3
                   ON customer_base_runs(planning_version_id, scenario_id)
                                     WHERE status IN ('prepared', 'importing', 'submitted')"""
            )

    def create(
        self,
        planning_version_id: str,
        scenario_id: str,
        requester: str,
        request: dict,
        result: dict,
    ) -> dict:
        now = _now()
        run_id = str(uuid.uuid4())
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                      """INSERT INTO customer_base_runs
                            (run_id, planning_version_id, scenario_id, requester, status, created_at, updated_at,
                        request_json, result_json)
                         VALUES (?, ?, ?, ?, 'prepared', ?, ?, ?, ?)""",
                          (run_id, planning_version_id, scenario_id, requester, now, now, _json(request), _json(result)),
                )
        except sqlite3.IntegrityError as exc:
            raise ActiveRunExists(
                f"Scenario {scenario_id!r} in planning version {planning_version_id!r} already has an active run"
            ) from exc
        return self.get(run_id, requester)

    def get(self, run_id: str, requester: str, *, admin: bool = False) -> dict:
        with closing(self._connect()) as connection:
            query = "SELECT * FROM customer_base_runs WHERE run_id = ?"
            params: tuple = (run_id,)
            if not admin:
                query += " AND requester = ?"
                params = (run_id, requester)
            row = connection.execute(query, params).fetchone()
        if row is None:
            raise RunNotFound("Run was not found or is not visible to this requester")
        return _row_to_dict(row)

    def prepared_runs_for_requester(self, requester: str) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM customer_base_runs WHERE requester = ? AND status = 'prepared'",
                (requester,),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def transition(
        self,
        run_id: str,
        requester: str,
        expected_status: str,
        status: str,
        *,
        result: dict | None = None,
        error: str | None = None,
    ) -> dict:
        now = _now()
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """UPDATE customer_base_runs
                   SET status = ?, updated_at = ?,
                       result_json = COALESCE(?, result_json), error = ?
                   WHERE run_id = ? AND requester = ? AND status = ?""",
                (
                    status,
                    now,
                    _json(result) if result is not None else None,
                    error,
                    run_id,
                    requester,
                    expected_status,
                ),
            )
            if cursor.rowcount != 1:
                raise RunNotFound("Run is not available to transition for this requester")
        return self.get(run_id, requester)

    def expire_prepared(self, max_age_minutes: int = 120) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """UPDATE customer_base_runs
                   SET status = 'expired', updated_at = ?
                   WHERE status = 'prepared' AND updated_at < ?""",
                (_now(), cutoff),
            )
            return cursor.rowcount

    def resolve_submitted(
        self,
        run_id: str,
        requester: str,
        *,
        completed: bool,
        result: dict | None = None,
        error: str | None = None,
    ) -> dict:
        """Release the version lock after external IBP reconciliation."""
        return self.transition(
            run_id,
            requester,
            "submitted",
            "completed" if completed else "failed",
            result=result,
            error=error,
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: dict) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "run_id": row["run_id"],
        "planning_version_id": row["planning_version_id"],
        "scenario_id": row["scenario_id"],
        "requester": row["requester"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "request": json.loads(row["request_json"]),
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "error": row["error"],
    }