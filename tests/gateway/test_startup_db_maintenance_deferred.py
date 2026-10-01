"""state.db maintenance must never block the gateway READY critical path."""

from __future__ import annotations

from types import SimpleNamespace

import gateway.run as gateway_run
import gateway.run_profile_reconcile as reconcile
from gateway.config import GatewayConfig


class _Ticks:
    """Stop event that lets housekeeping run exactly count ticks without sleeping."""

    def __init__(self, count: int):
        self.count = count
        self.done = 0

    def is_set(self):
        return self.done >= self.count

    def wait(self, timeout=None):
        self.done += 1
        return True


def test_session_db_init_does_not_run_retention_or_vacuum_maintenance(monkeypatch, tmp_path):
    """Opening the runner session DB stays cheap; maintenance belongs to housekeeping."""
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = GatewayConfig(sessions_dir=tmp_path / "sessions")
    monkeypatch.setattr(
        gateway_run.GatewayRunner,
        "_open_session_db_for_active_scope",
        lambda self, raise_on_error=False: None,
    )
    calls = []
    monkeypatch.setattr(
        gateway_run,
        "_housekeeping_state_db_maintenance",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        reconcile,
        "_for_each_served_profile",
        lambda runner, fn: fn("default"),
    )

    runner._init_session_db()

    assert calls == []


def test_state_db_maintenance_runs_only_on_hourly_housekeeping_tick(monkeypatch, tmp_path):
    """Maintenance is deferred beyond READY; state_meta owns the durable cadence."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(gateway_run, "_lower_housekeeping_thread_priority", lambda: None)
    for name in (
        "_write_runtime_status_quiet",
        "_housekeeping_channel_directory",
        "_housekeeping_media_caches",
        "_housekeeping_paste_sweep",
        "_housekeeping_misfire_catch_up",
        "_housekeeping_curator",
        "_housekeeping_skill_sync",
        "_housekeeping_org_skill_sync",
        "_housekeeping_plugin_update_check",
        "_housekeeping_deferred_fts_retry",
        "_housekeeping_memory_trim",
        "_housekeeping_checkpoint_prune",
    ):
        monkeypatch.setattr(gateway_run, name, lambda *args, **kwargs: None)

    monkeypatch.setattr(reconcile, "profile_scoped_chore", lambda runner, fn: fn)
    monkeypatch.setattr(reconcile, "_mcp_config_reconciler", lambda runner: (lambda: None))

    calls = []
    monkeypatch.setattr(
        gateway_run,
        "_housekeeping_state_db_maintenance",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    runner = SimpleNamespace(config=GatewayConfig(sessions_dir=tmp_path / "sessions"))

    gateway_run._start_gateway_housekeeping(_Ticks(59), interval=0, runner=runner)
    assert calls == []

    gateway_run._start_gateway_housekeeping(_Ticks(60), interval=0, runner=runner)
    assert len(calls) == 1
