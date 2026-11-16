"""Checkpoint store tests: state_type persistence and schema migration."""

from __future__ import annotations

import sqlite3

from qitos.checkpoint import (
    Checkpoint,
    CheckpointConfig,
    CheckpointId,
    InMemoryCheckpointStore,
    SqliteCheckpointStore,
)


def _checkpoint(thread_id: str = "t1", step: int = 0) -> Checkpoint:
    return Checkpoint(
        id=CheckpointId(f"cp-{step}"),
        thread_id=thread_id,
        step=step,
        state_data={"task": "hello", "current_step": step},
        state_type="some.module.SomeState",
    )


class TestCheckpointStateType:
    def test_dict_round_trip_keeps_state_type(self):
        cp = _checkpoint()
        restored = Checkpoint.from_dict(cp.to_dict())
        assert restored.state_type == "some.module.SomeState"

    def test_dict_round_trip_defaults_to_none(self):
        cp = Checkpoint(id=CheckpointId("x"), thread_id="t", step=0, state_data={})
        assert cp.to_dict()["state_type"] is None
        assert Checkpoint.from_dict(cp.to_dict()).state_type is None

    def test_in_memory_store_keeps_state_type(self):
        store = InMemoryCheckpointStore()
        store.put(CheckpointConfig(thread_id="t1"), _checkpoint(), {"source": "loop"}, {})
        got = store.get(CheckpointConfig(thread_id="t1"))
        assert got is not None
        assert got.state_type == "some.module.SomeState"

    def test_sqlite_store_keeps_state_type(self, tmp_path):
        with SqliteCheckpointStore(str(tmp_path / "cp.sqlite")) as store:
            store.put(
                CheckpointConfig(thread_id="t1"), _checkpoint(), {"source": "loop"}, {}
            )
            got = store.get(CheckpointConfig(thread_id="t1"))
            assert got is not None
            assert got.state_type == "some.module.SomeState"
            listed = list(store.list(CheckpointConfig(thread_id="t1")))
            assert [item.checkpoint.state_type for item in listed] == [
                "some.module.SomeState"
            ]

    def test_sqlite_store_migrates_pre_state_type_database(self, tmp_path):
        db_path = tmp_path / "legacy.sqlite"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE checkpoints (
                checkpoint_id  TEXT PRIMARY KEY,
                thread_id      TEXT NOT NULL,
                step           INTEGER NOT NULL,
                state_data     TEXT NOT NULL,
                state_versions TEXT NOT NULL DEFAULT '{}',
                versions_seen  TEXT NOT NULL DEFAULT '{}',
                parent_id      TEXT,
                created_at     TEXT NOT NULL,
                schema_version TEXT NOT NULL DEFAULT 'v2'
            );
            """
        )
        conn.commit()
        conn.close()

        with SqliteCheckpointStore(str(db_path)) as store:
            store.put(
                CheckpointConfig(thread_id="t1"), _checkpoint(), {"source": "loop"}, {}
            )
            got = store.get(CheckpointConfig(thread_id="t1"))
            assert got is not None
            assert got.state_type == "some.module.SomeState"
