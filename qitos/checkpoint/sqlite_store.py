"""SQLite-backed checkpoint store with WAL mode.

Provides durable, file-based checkpoint persistence suitable for
production use.  Uses WAL mode for concurrent read/write support.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterator, List, Optional, Sequence

from .store import (
    Checkpoint,
    CheckpointConfig,
    CheckpointId,
    CheckpointMetadata,
    CheckpointStore,
    CheckpointTuple,
    PendingWrite,
    StateVersions,
)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id  TEXT PRIMARY KEY,
    thread_id      TEXT NOT NULL,
    step           INTEGER NOT NULL,
    state_data     TEXT NOT NULL,          -- JSON
    state_versions TEXT NOT NULL DEFAULT '{}',  -- JSON
    versions_seen  TEXT NOT NULL DEFAULT '{}',  -- JSON
    parent_id      TEXT,
    created_at     TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT 'v2',
    state_type     TEXT
);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    write_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_id TEXT NOT NULL REFERENCES checkpoints(checkpoint_id),
    task_id       TEXT NOT NULL,
    channel       TEXT NOT NULL,
    value         TEXT,                     -- JSON (nullable)
    UNIQUE(checkpoint_id, task_id, channel)
);

CREATE TABLE IF NOT EXISTS checkpoint_metadata (
    checkpoint_id TEXT PRIMARY KEY REFERENCES checkpoints(checkpoint_id),
    source        TEXT,
    step_int      INTEGER,
    parents       TEXT DEFAULT '{}',        -- JSON
    run_id        TEXT
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_thread
    ON checkpoints(thread_id, step);

CREATE INDEX IF NOT EXISTS idx_writes_checkpoint
    ON checkpoint_writes(checkpoint_id);
"""


class SqliteCheckpointStore(CheckpointStore):
    """SQLite-backed checkpoint store with WAL mode.

    Args:
        db_path: Path to the SQLite database file.
            Use ``":memory:"`` for an in-memory database (testing only).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._migrate_schema()
        self._conn.commit()

    def _migrate_schema(self) -> None:
        """Add columns introduced after a database was created."""
        columns = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(checkpoints)").fetchall()
        }
        if "state_type" not in columns:
            self._conn.execute("ALTER TABLE checkpoints ADD COLUMN state_type TEXT")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> SqliteCheckpointStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ---- internal helpers ----

    def _row_to_checkpoint(self, row: tuple) -> Checkpoint:
        (
            cp_id, thread_id, step,
            state_data_json, state_versions_json, versions_seen_json,
            parent_id, created_at, schema_version, state_type,
        ) = row
        return Checkpoint(
            id=CheckpointId(cp_id),
            thread_id=thread_id,
            step=step,
            state_data=json.loads(state_data_json),
            state_versions=json.loads(state_versions_json),
            versions_seen=json.loads(versions_seen_json),
            parent_id=parent_id,
            created_at=created_at,
            schema_version=schema_version,
            state_type=state_type,
        )

    def _load_pending_writes(self, checkpoint_id: str) -> List[PendingWrite]:
        cur = self._conn.execute(
            "SELECT task_id, channel, value FROM checkpoint_writes "
            "WHERE checkpoint_id = ?",
            (checkpoint_id,),
        )
        writes = []
        for task_id, channel, value_json in cur.fetchall():
            value = json.loads(value_json) if value_json is not None else None
            writes.append(PendingWrite(task_id=task_id, channel=channel, value=value))
        return writes

    def _load_metadata(self, checkpoint_id: str) -> CheckpointMetadata:
        cur = self._conn.execute(
            "SELECT source, step_int, parents, run_id FROM checkpoint_metadata "
            "WHERE checkpoint_id = ?",
            (checkpoint_id,),
        )
        row = cur.fetchone()
        if row is None:
            return CheckpointMetadata()
        source, step_int, parents_json, run_id = row
        meta: CheckpointMetadata = {}
        if source is not None:
            meta["source"] = source
        if step_int is not None:
            meta["step"] = step_int
        if parents_json is not None:
            meta["parents"] = json.loads(parents_json)
        if run_id is not None:
            meta["run_id"] = run_id
        return meta

    def _resolve_config(self, config: CheckpointConfig) -> Optional[CheckpointId]:
        if config.checkpoint_id is not None:
            return config.checkpoint_id
        # find latest for thread
        cur = self._conn.execute(
            "SELECT checkpoint_id FROM checkpoints "
            "WHERE thread_id = ? ORDER BY step DESC, rowid DESC LIMIT 1",
            (config.thread_id,),
        )
        row = cur.fetchone()
        return CheckpointId(row[0]) if row else None

    def _find_parent_config(self, thread_id: str, parent_id: Optional[str]) -> Optional[CheckpointConfig]:
        if parent_id is None:
            return None
        return CheckpointConfig(thread_id=thread_id, checkpoint_id=CheckpointId(parent_id))

    # ---- sync interface ----

    def put(
        self,
        config: CheckpointConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: StateVersions,
    ) -> CheckpointConfig:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO checkpoints "
                "(checkpoint_id, thread_id, step, state_data, state_versions, "
                "versions_seen, parent_id, created_at, schema_version, state_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    checkpoint.id,
                    checkpoint.thread_id,
                    checkpoint.step,
                    json.dumps(checkpoint.state_data, ensure_ascii=False),
                    json.dumps(checkpoint.state_versions, ensure_ascii=False),
                    json.dumps(checkpoint.versions_seen, ensure_ascii=False),
                    checkpoint.parent_id,
                    checkpoint.created_at,
                    checkpoint.schema_version,
                    checkpoint.state_type,
                ),
            )
            # metadata
            self._conn.execute(
                "INSERT OR REPLACE INTO checkpoint_metadata "
                "(checkpoint_id, source, step_int, parents, run_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    checkpoint.id,
                    metadata.get("source"),
                    metadata.get("step"),
                    json.dumps(metadata.get("parents", {}), ensure_ascii=False),
                    metadata.get("run_id"),
                ),
            )

        return CheckpointConfig(
            thread_id=config.thread_id, checkpoint_id=checkpoint.id
        )

    def get_tuple(self, config: CheckpointConfig) -> Optional[CheckpointTuple]:
        cp_id = self._resolve_config(config)
        if cp_id is None:
            return None
        cur = self._conn.execute(
            "SELECT checkpoint_id, thread_id, step, state_data, state_versions, "
            "versions_seen, parent_id, created_at, schema_version, state_type "
            "FROM checkpoints WHERE checkpoint_id = ?",
            (cp_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        checkpoint = self._row_to_checkpoint(row)
        pending_writes = self._load_pending_writes(cp_id)
        meta = self._load_metadata(cp_id)
        parent_config = self._find_parent_config(checkpoint.thread_id, checkpoint.parent_id)

        return CheckpointTuple(
            config=CheckpointConfig(
                thread_id=checkpoint.thread_id, checkpoint_id=checkpoint.id
            ),
            checkpoint=checkpoint,
            metadata=meta,
            parent_config=parent_config,
            pending_writes=pending_writes if pending_writes else None,
        )

    def list(
        self,
        config: CheckpointConfig,
        *,
        limit: Optional[int] = None,
        before: Optional[CheckpointConfig] = None,
    ) -> Iterator[CheckpointTuple]:
        params: list = [config.thread_id]
        sql = (
            "SELECT checkpoint_id, thread_id, step, state_data, state_versions, "
            "versions_seen, parent_id, created_at, schema_version, state_type "
            "FROM checkpoints WHERE thread_id = ?"
        )
        if before is not None and before.checkpoint_id is not None:
            sql += " AND step < (SELECT step FROM checkpoints WHERE checkpoint_id = ?)"
            params.append(before.checkpoint_id)
        sql += " ORDER BY step DESC, rowid DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        cur = self._conn.execute(sql, params)
        for row in cur.fetchall():
            checkpoint = self._row_to_checkpoint(row)
            pending_writes = self._load_pending_writes(checkpoint.id)
            meta = self._load_metadata(checkpoint.id)
            parent_config = self._find_parent_config(
                checkpoint.thread_id, checkpoint.parent_id
            )
            yield CheckpointTuple(
                config=CheckpointConfig(
                    thread_id=checkpoint.thread_id,
                    checkpoint_id=checkpoint.id,
                ),
                checkpoint=checkpoint,
                metadata=meta,
                parent_config=parent_config,
                pending_writes=pending_writes if pending_writes else None,
            )

    def put_writes(
        self,
        config: CheckpointConfig,
        writes: Sequence[PendingWrite],
        task_id: str,
    ) -> None:
        cp_id = self._resolve_config(config)
        if cp_id is None:
            return
        with self._conn:
            for w in writes:
                self._conn.execute(
                    "INSERT OR REPLACE INTO checkpoint_writes "
                    "(checkpoint_id, task_id, channel, value) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        cp_id,
                        w.task_id,
                        w.channel,
                        json.dumps(w.value, ensure_ascii=False) if w.value is not None else None,
                    ),
                )

    def delete(self, config: CheckpointConfig) -> None:
        cp_id = self._resolve_config(config)
        if cp_id is None:
            return
        with self._conn:
            self._conn.execute(
                "DELETE FROM checkpoint_writes WHERE checkpoint_id = ?", (cp_id,)
            )
            self._conn.execute(
                "DELETE FROM checkpoint_metadata WHERE checkpoint_id = ?", (cp_id,)
            )
            self._conn.execute(
                "DELETE FROM checkpoints WHERE checkpoint_id = ?", (cp_id,)
            )


__all__ = ["SqliteCheckpointStore"]
