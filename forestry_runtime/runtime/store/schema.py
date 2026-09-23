"""Schema creation and migration for the Run store.

This is the only place that knows the persistent layout, so a schema change is a
one-file diff instead of an edit buried in ``RunStore.__init__``. It is also the
only part of the store that can be tested without driving a Run.

Migrations are additive and idempotent: every statement is ``IF NOT EXISTS`` or
guarded by a ``PRAGMA table_info`` check, so opening an older database upgrades it
in place. There is no version table, and this module deliberately does not add
one -- the guarded form is what existing databases rely on, and introducing a
version counter would need a migration of its own.
"""

from __future__ import annotations

import sqlite3


TABLES = (
    """CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat_id TEXT NOT NULL,
    state TEXT NOT NULL, messages_json TEXT NOT NULL, asset_ids_json TEXT NOT NULL,
    use_tools INTEGER NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
    pause_requested INTEGER NOT NULL DEFAULT 0,
    model_calls INTEGER NOT NULL DEFAULT 0, final_json TEXT,
    agent_input_count INTEGER NOT NULL DEFAULT 0,
    state_version INTEGER NOT NULL DEFAULT 0, recovery_reason TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL, event_json TEXT NOT NULL,
    created_at REAL NOT NULL, turn_id TEXT, PRIMARY KEY(run_id, seq))""",
    """CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL, result_json TEXT, state TEXT NOT NULL,
    started_at REAL NOT NULL, finished_at REAL, turn_id TEXT)""",
    """CREATE TABLE IF NOT EXISTS job_refs (
    run_id TEXT NOT NULL, job_id TEXT NOT NULL, job_type TEXT,
    state TEXT, turn_id TEXT, action_id TEXT,
    PRIMARY KEY(run_id, job_id))""",
    """CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
    agent_run_id TEXT NOT NULL UNIQUE, input_start INTEGER NOT NULL,
    input_end INTEGER NOT NULL, state TEXT NOT NULL,
    checkpoint_state TEXT NOT NULL DEFAULT 'unknown',
    resume_agent_run_id TEXT, input_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    UNIQUE(run_id, ordinal))""",
    """CREATE TABLE IF NOT EXISTS inputs (
    id TEXT NOT NULL, owner TEXT NOT NULL, chat_id TEXT NOT NULL,
    run_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
    asset_ids_json TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL,
    turn_id TEXT, ordinal INTEGER NOT NULL, created_at REAL NOT NULL,
    reserved_at REAL, consumed_at REAL,
    PRIMARY KEY(owner,chat_id,id))""",
    """CREATE TABLE IF NOT EXISTS steps (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, turn_id TEXT NOT NULL,
    number INTEGER NOT NULL, context_json TEXT NOT NULL,
    estimated_tokens INTEGER, provider_usage_json TEXT,
    created_at REAL NOT NULL, finished_at REAL,
    UNIQUE(turn_id,number))""",
    """CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY, action_id TEXT NOT NULL, run_id TEXT NOT NULL,
    turn_id TEXT, ordinal INTEGER NOT NULL, state TEXT NOT NULL,
    operation_started INTEGER NOT NULL DEFAULT 0,
    result_json TEXT, error_json TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    UNIQUE(action_id,ordinal))""",
    """CREATE TABLE IF NOT EXISTS artifact_refs (
    run_id TEXT NOT NULL, turn_id TEXT NOT NULL, asset_id TEXT NOT NULL,
    job_id TEXT, action_id TEXT, created_at REAL NOT NULL,
    PRIMARY KEY(run_id, turn_id, asset_id))""",
)

# Columns added after the first release. Each is applied only when absent.
ADDED_COLUMNS = (
    ("runs", "agent_input_count", "INTEGER NOT NULL DEFAULT 0"),
    ("runs", "pause_requested", "INTEGER NOT NULL DEFAULT 0"),
    ("runs", "state_version", "INTEGER NOT NULL DEFAULT 0"),
    ("runs", "recovery_reason", "TEXT"),
    ("events", "turn_id", "TEXT"),
    ("actions", "turn_id", "TEXT"),
    ("job_refs", "turn_id", "TEXT"),
    ("job_refs", "action_id", "TEXT"),
    ("turns", "resume_agent_run_id", "TEXT"),
    ("turns", "input_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS run_chat ON runs(owner, chat_id, created_at)",
    "CREATE INDEX IF NOT EXISTS event_turn ON events(run_id, turn_id, seq)",
    "CREATE INDEX IF NOT EXISTS action_turn ON actions(run_id, turn_id)",
    "CREATE INDEX IF NOT EXISTS turn_run ON turns(run_id, ordinal)",
    "CREATE INDEX IF NOT EXISTS input_run ON inputs(run_id, ordinal)",
    "CREATE INDEX IF NOT EXISTS attempt_action ON attempts(action_id, ordinal)",
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def initialize(conn: sqlite3.Connection) -> None:
    """Create the schema and apply additive migrations. Safe to call every start."""
    for statement in TABLES:
        conn.execute(statement)
    for table, column, declaration in ADDED_COLUMNS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    for statement in INDEXES:
        conn.execute(statement)


__all__ = ["ADDED_COLUMNS", "INDEXES", "TABLES", "initialize"]
