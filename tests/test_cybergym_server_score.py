from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cybergym_server_score.py"
_spec = importlib.util.spec_from_file_location("cybergym_server_score", SCRIPT)
assert _spec and _spec.loader
server_score = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = server_score
_spec.loader.exec_module(server_score)


def _conn(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE poc_records ("
        "task_id TEXT, vul_exit_code INTEGER, fix_exit_code INTEGER, "
        "created_at TEXT, poc_length INTEGER)"
    )
    conn.executemany(
        "INSERT INTO poc_records VALUES (?, ?, ?, ?, ?)",
        [(task, vul, fix, created, length) for task, vul, fix, created, length in rows],
    )
    return conn


def test_pass_rule_and_missing_tasks():
    conn = _conn(
        [
            ("arvo:pass", 1, 0, "2026-11-06 07:56:19.000000", 40023),
            ("arvo:nocrash", 0, None, "2026-11-06 07:55:39.000000", 5),
            ("arvo:timeout", 300, 0, "2026-11-06 07:55:00.000000", 10),
        ]
    )
    scores = server_score.score_tasks(conn, ["arvo:pass", "arvo:nocrash", "arvo:timeout", "arvo:missing"])
    assert [s.passed for s in scores] == [True, False, False, False]
    assert [s.attempts for s in scores] == [1, 1, 1, 0]
    assert scores[1].vul_exit_code == 0
    assert scores[1].fix_exit_code is None


def test_since_filter_and_latest_passing_row():
    conn = _conn(
        [
            ("arvo:a", 0, None, "2026-11-06 07:00:00.000000", 3),
            ("arvo:a", 1, 0, "2026-11-06 08:00:00.000000", 4),
        ]
    )
    all_scores = server_score.score_task(conn, "arvo:a")
    assert all_scores.passed is True
    assert all_scores.attempts == 2

    recent = server_score.score_task(conn, "arvo:a", since="2026-11-06 07:30:00")
    assert recent.passed is True
    assert recent.attempts == 1
    assert recent.created_at == "2026-11-06 08:00:00.000000"

    before = server_score.score_task(conn, "arvo:a", since="2026-11-06 09:00:00")
    assert before.passed is False
    assert before.attempts == 0


def test_read_task_file(tmp_path: Path):
    path = tmp_path / "tasks.txt"
    path.write_text("arvo:1\n\n# comment\narvo:2\n", encoding="utf-8")
    assert server_score._read_task_file(path) == ["arvo:1", "arvo:2"]
