"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_ci_notification_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the real Actions/API notification env so pipeline tests never write to the
    live job summary or GitHub API (CI itself sets GITHUB_STEP_SUMMARY for every step)."""
    for var in ("GITHUB_STEP_SUMMARY", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
