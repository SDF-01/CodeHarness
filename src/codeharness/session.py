"""SQLite session store. The full history stays here."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class StoredMessage:
    role: str
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_calls: list[dict] | None = None


@dataclass
class Session:
    id: str
    title: str
    project_root: str
    messages: list[StoredMessage] = field(default_factory=list)


@dataclass(frozen=True)
class SessionSummary:
    id: str
    title: str
    updated_at: str


_KNOWN_AGENTS = {"plan", "build", "route", "page", "api", "review", "explore", "general"}


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                project_root TEXT NOT NULL,
                title TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_call_id TEXT,
                tool_name TEXT,
                tool_calls_json TEXT,
                UNIQUE (session_id, seq),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS session_state (
                session_id TEXT PRIMARY KEY,
                agent TEXT NOT NULL DEFAULT 'build',
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                text TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS work_dirs (
                session_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS message_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                text TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(session_state)")}
        if "phase" not in columns:
            self._conn.execute("ALTER TABLE session_state ADD COLUMN phase TEXT NOT NULL DEFAULT 'ready'")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def create(self, project_root: Path) -> Session:
        now = _now()
        session = Session(id=uuid.uuid4().hex[:12], title="new session", project_root=str(project_root))
        self._conn.execute(
            "INSERT INTO sessions (id, created_at, updated_at, project_root, title) VALUES (?, ?, ?, ?, ?)",
            (session.id, now, now, session.project_root, session.title),
        )
        self._conn.commit()
        return session

    def get(self, session_id: str) -> Session:
        row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        messages = [
            _row_to_message(item)
            for item in self._conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY seq",
                (session_id,),
            )
        ]
        return Session(
            id=row["id"],
            title=row["title"],
            project_root=row["project_root"],
            messages=messages,
        )

    def list_sessions(self) -> list[SessionSummary]:
        rows = self._conn.execute("SELECT id, title, updated_at FROM sessions ORDER BY updated_at DESC")
        return [SessionSummary(id=row["id"], title=row["title"], updated_at=row["updated_at"]) for row in rows]

    def append(self, session: Session, message: StoredMessage) -> None:
        seq = len(session.messages) + 1
        tool_calls_json = json.dumps(message.tool_calls) if message.tool_calls else None
        self._conn.execute(
            """
            INSERT INTO messages (session_id, seq, role, content, tool_call_id, tool_name, tool_calls_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                seq,
                message.role,
                message.content,
                message.tool_call_id,
                message.tool_name,
                tool_calls_json,
            ),
        )
        now = _now()
        self._conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session.id))
        self._conn.commit()
        session.messages.append(message)

    def get_agent(self, session: Session) -> str:
        row = self._conn.execute(
            "SELECT agent FROM session_state WHERE session_id = ?",
            (session.id,),
        ).fetchone()
        if row is None:
            return "build"
        return str(row["agent"] or "build")

    def set_agent(self, session: Session, name: str) -> None:
        agent = name if name in _KNOWN_AGENTS else "build"
        self._conn.execute(
            """
            INSERT INTO session_state (session_id, agent) VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET agent = excluded.agent
            """,
            (session.id, agent),
        )
        self._conn.commit()

    def add_lesson(self, session: Session, text: str) -> None:
        lesson = " ".join(text.split())[:160]
        if not lesson:
            return
        existing = self.lessons(session)
        if existing and existing[-1] == lesson:
            return
        self._conn.execute(
            "INSERT INTO lessons (session_id, text) VALUES (?, ?)",
            (session.id, lesson),
        )
        self._conn.commit()

    def lessons(self, session: Session, limit: int = 3) -> list[str]:
        rows = self._conn.execute(
            "SELECT text FROM lessons WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session.id, limit),
        ).fetchall()
        return [str(row["text"]) for row in reversed(rows)]

    def work_dir(self, session: Session) -> str:
        row = self._conn.execute(
            "SELECT path FROM work_dirs WHERE session_id = ?",
            (session.id,),
        ).fetchone()
        if row is None:
            return ""
        return str(row["path"])

    def set_work_dir(self, session: Session, path: Path) -> None:
        self._conn.execute(
            """
            INSERT INTO work_dirs (session_id, path) VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET path = excluded.path
            """,
            (session.id, str(path.resolve())),
        )
        self._conn.commit()

    def set_phase(self, session: Session, phase: str) -> None:
        self._conn.execute(
            """
            INSERT INTO session_state (session_id, agent, phase) VALUES (?, 'build', ?)
            ON CONFLICT(session_id) DO UPDATE SET phase = excluded.phase
            """,
            (session.id, phase),
        )
        self._conn.commit()

    def get_phase(self, session: Session) -> str:
        row = self._conn.execute(
            "SELECT phase FROM session_state WHERE session_id = ?",
            (session.id,),
        ).fetchone()
        if row is None or row["phase"] is None:
            return "ready"
        return str(row["phase"])

    def enqueue(self, session: Session, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        self._conn.execute(
            "INSERT INTO message_queue (session_id, text) VALUES (?, ?)",
            (session.id, cleaned),
        )
        self._conn.commit()

    def dequeue(self, session: Session) -> str:
        row = self._conn.execute(
            "SELECT id, text FROM message_queue WHERE session_id = ? ORDER BY id LIMIT 1",
            (session.id,),
        ).fetchone()
        if row is None:
            return ""
        self._conn.execute("DELETE FROM message_queue WHERE id = ?", (row["id"],))
        self._conn.commit()
        return str(row["text"])

    def queued(self, session: Session) -> list[str]:
        rows = self._conn.execute(
            "SELECT text FROM message_queue WHERE session_id = ? ORDER BY id",
            (session.id,),
        ).fetchall()
        return [str(row["text"]) for row in rows]

    def drop_interrupted(self, session: Session) -> None:
        if not session.messages:
            return
        last = session.messages[-1]
        if last.role != "assistant" or last.content != "interrupted":
            return
        self._conn.execute(
            "DELETE FROM messages WHERE session_id = ? AND seq = ?",
            (session.id, len(session.messages)),
        )
        self._conn.commit()
        session.messages.pop()

    def set_title(self, session: Session, title: str) -> None:
        session.title = title
        self._conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session.id),
        )
        self._conn.commit()


def database_path(project_root: Path) -> Path:
    return project_root / ".codeharness" / "sessions.db"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_message(row: sqlite3.Row) -> StoredMessage:
    raw_calls = row["tool_calls_json"]
    return StoredMessage(
        role=row["role"],
        content=row["content"],
        tool_call_id=row["tool_call_id"],
        tool_name=row["tool_name"],
        tool_calls=json.loads(raw_calls) if raw_calls else None,
    )
