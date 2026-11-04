from __future__ import annotations

import os

from qitos.kit.env.docker_env import _health_check_timeout


def test_health_check_timeout_defaults_to_60(monkeypatch):
    monkeypatch.delenv("QITOS_DOCKER_HEALTH_TIMEOUT", raising=False)
    assert _health_check_timeout() == 60


def test_health_check_timeout_env_override(monkeypatch):
    monkeypatch.setenv("QITOS_DOCKER_HEALTH_TIMEOUT", "120")
    assert _health_check_timeout() == 120


def test_health_check_timeout_ignores_invalid_env(monkeypatch):
    monkeypatch.setenv("QITOS_DOCKER_HEALTH_TIMEOUT", "not-a-number")
    assert _health_check_timeout() == 60

    monkeypatch.setenv("QITOS_DOCKER_HEALTH_TIMEOUT", "0")
    assert _health_check_timeout() == 60

    monkeypatch.setenv("QITOS_DOCKER_HEALTH_TIMEOUT", "")
    assert _health_check_timeout() == 60


def test_health_check_timeout_reads_process_env(monkeypatch):
    monkeypatch.setitem(os.environ, "QITOS_DOCKER_HEALTH_TIMEOUT", "90")
    assert _health_check_timeout() == 90
