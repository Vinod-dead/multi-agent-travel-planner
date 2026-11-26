"""SQLite storage for plans (so history survives a restart). Standard library only."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from travel_planner.models import TripPlan, TripRequest
from travel_planner.planner import STEPS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status     TEXT NOT NULL,
    request    TEXT NOT NULL,
    progress   TEXT NOT NULL,
    result     TEXT,
    error      TEXT
);
CREATE INDEX IF NOT EXISTS plans_created ON plans(created_at DESC);
"""

ACTIVE = ("queued", "running")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _blank_progress() -> dict[str, Any]:
    return {key: {"label": label, "status": "pending", "message": ""} for key, label in STEPS}


class PlanStore:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- writes -----------------------------------------------------------------
    def create(self, request: TripRequest) -> str:
        plan_id = uuid.uuid4().hex[:12]
        now = _now()
        with self._lock:
            self._db.execute(
                "INSERT INTO plans (id, created_at, updated_at, status, request, progress) VALUES (?,?,?,?,?,?)",
                (plan_id, now, now, "queued", request.model_dump_json(), json.dumps(_blank_progress())),
            )
            self._db.commit()
        return plan_id

    def set_status(self, plan_id: str, status: str) -> None:
        with self._lock:
            self._db.execute("UPDATE plans SET status=?, updated_at=? WHERE id=?", (status, _now(), plan_id))
            self._db.commit()

    def update_progress(self, plan_id: str, agent: str, status: str, message: str) -> None:
        with self._lock:
            row = self._db.execute("SELECT progress FROM plans WHERE id=?", (plan_id,)).fetchone()
            if not row:
                return
            progress = json.loads(row["progress"])
            step = progress.setdefault(agent, {"label": agent.title(), "status": "pending", "message": ""})
            step["status"], step["message"] = status, message
            self._db.execute("UPDATE plans SET progress=?, updated_at=? WHERE id=?", (json.dumps(progress), _now(), plan_id))
            self._db.commit()

    def set_result(self, plan_id: str, plan: TripPlan) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE plans SET status='done', result=?, error=NULL, updated_at=? WHERE id=?",
                (plan.model_dump_json(), _now(), plan_id),
            )
            self._db.commit()

    def set_error(self, plan_id: str, message: str, status: str = "failed") -> None:
        with self._lock:
            self._db.execute("UPDATE plans SET status=?, error=?, updated_at=? WHERE id=?", (status, message, _now(), plan_id))
            self._db.commit()

    def delete(self, plan_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM plans WHERE id=?", (plan_id,))
            self._db.commit()
            return cur.rowcount > 0

    def fail_stale(self) -> int:
        """After a restart nothing is really running any more."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE plans SET status='failed', error='The server restarted while this plan was running.', updated_at=? "
                "WHERE status IN ('queued','running')",
                (_now(),),
            )
            self._db.commit()
            return cur.rowcount

    # -- reads ------------------------------------------------------------------
    def get(self, plan_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "status": row["status"],
            "request": json.loads(row["request"]),
            "progress": json.loads(row["progress"]),
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
        }

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, created_at, status, request, result FROM plans ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        out = []
        for row in rows:
            request = json.loads(row["request"])
            result = json.loads(row["result"]) if row["result"] else None
            out.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "status": row["status"],
                    "destination": (result or {}).get("destination", {}).get("name") or request.get("destination") or "Surprise me",
                    "origin": request.get("origin", ""),
                    "days": request.get("days"),
                    "budget": request.get("budget"),
                    "currency": request.get("currency"),
                    "mode": (result or {}).get("mode"),
                }
            )
        return out
