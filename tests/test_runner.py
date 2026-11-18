# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import contextlib
import datetime
from types import SimpleNamespace

import pytest

from wazo_schedule_holidays import runner
from wazo_schedule_holidays.exceptions import ConfigNotFound, SyncInProgress
from wazo_schedule_holidays.runner import resolve_owner, sync_all
from wazo_schedule_holidays.service import SyncResult
from wazo_schedule_holidays.storage import ConfigStorage, HolidayConfig

T1 = "11111111-1111-1111-1111-111111111111"
T2 = "22222222-2222-2222-2222-222222222222"


class NotFound(Exception):
    pass


class Harness:
    def __init__(self, tmp_path):
        self.storage = ConfigStorage(tmp_path / "holidays")
        self.events = []
        self.behaviour = {}
        self.service = SimpleNamespace(sync=self._sync)

    def configure(self, schedule_id, tenant=T1):
        self.storage.save(schedule_id, tenant, HolidayConfig(country="NL"))

    def _sync(self, schedule_id, tenant_uuids, dry_run=False):
        self.events.append(("sync", schedule_id, tenant_uuids, dry_run))
        action = self.behaviour.get(schedule_id)
        if isinstance(action, BaseException):
            raise action
        return SyncResult(
            dry_run=dry_run,
            today=datetime.date(2026, 11, 18),
            changed=True,
            added=[],
            removed=[],
            managed=[],
        )

    @contextlib.contextmanager
    def scope(self):
        self.events.append("enter")
        try:
            yield
        except BaseException:
            self.events.append("scope-error")
            raise
        self.events.append("exit")

    def run(self, **kwargs):
        return sync_all(
            self.service,
            self.storage,
            scope=self.scope,
            flush=lambda: self.events.append("flush"),
            rollback=lambda: self.events.append("rollback"),
            not_found=(NotFound,),
            **kwargs,
        )


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def test_no_configuration_is_an_empty_successful_run(h):
    report = h.run()

    assert report.ok
    assert report.results == {} and report.failed == {} and report.orphans == []
    assert h.events == []


def test_every_configured_schedule_is_synced_with_its_own_tenant(h):
    h.configure(3, T2)
    h.configure(1, T1)

    report = h.run()

    assert [e for e in h.events if isinstance(e, tuple)] == [
        ("sync", 1, [T1], False),
        ("sync", 3, [T2], False),
    ]
    assert sorted(report.results) == [1, 3]
    assert report.ok


def test_events_are_flushed_only_after_the_transaction_is_committed(h):
    h.configure(1)

    h.run()

    assert h.events == ["enter", ("sync", 1, [T1], False), "exit", "flush"]


def test_a_failing_schedule_does_not_stop_the_others(h):
    for schedule_id in (1, 2, 3):
        h.configure(schedule_id)
    h.behaviour[2] = RuntimeError("db down")

    report = h.run()

    assert sorted(report.results) == [1, 3]
    assert report.failed == {2: "db down"}
    assert not report.ok
    assert h.events.count("rollback") == 1
    assert h.events.count("flush") == 2


def test_schedule_busy_is_reported_as_a_failure(h):
    h.configure(1)
    h.behaviour[1] = SyncInProgress(1)

    report = h.run()

    assert 1 in report.failed and "retry" in report.failed[1]
    assert "rollback" in h.events


def test_missing_schedule_removes_its_orphan_configuration(h):
    h.configure(1)
    h.configure(2)
    h.behaviour[2] = NotFound("gone")

    report = h.run()

    assert report.orphans == [2]
    assert h.storage.list_schedule_ids() == [1]
    assert report.ok


def test_dry_run_is_forwarded_and_never_deletes_orphans(h):
    h.configure(2)
    h.behaviour[2] = NotFound("gone")

    report = h.run(dry_run=True)

    assert report.orphans == [2]
    assert h.storage.list_schedule_ids() == [2]
    assert ("sync", 2, [T1], True) in h.events


def test_configuration_deleted_meanwhile_is_ignored(h):
    h.configure(1)
    h.behaviour[1] = ConfigNotFound(1)

    report = h.run()

    assert report.ok and report.failed == {}


def test_corrupted_file_is_reported_and_the_others_still_run(h):
    h.configure(1)
    h.storage.path(2).write_text("{broken")

    report = h.run()

    assert 2 in report.failed and 1 in report.results
    assert not report.ok


def test_only_the_requested_schedule_is_synced(h):
    h.configure(1)
    h.configure(2)

    report = h.run(schedule_id=2)

    assert [e for e in h.events if isinstance(e, tuple)] == [("sync", 2, [T1], False)]
    assert list(report.results) == [2]


def test_requested_schedule_without_configuration_is_a_failure(h):
    report = h.run(schedule_id=7)

    assert 7 in report.failed and "configuration" in report.failed[7]
    assert not report.ok


def test_summary_counts(h):
    h.configure(1)
    h.configure(2)
    h.behaviour[2] = RuntimeError("boom")

    report = h.run()

    assert report.summary() == "1 synchronised (1 changed), 1 failed, 0 orphaned"


def fake_pwd(monkeypatch, entries):
    def getpwnam(name):
        if name not in entries:
            raise KeyError(name)
        return SimpleNamespace(pw_uid=entries[name][0], pw_gid=entries[name][1])

    monkeypatch.setattr(runner.pwd, "getpwnam", getpwnam)


def test_resolve_owner_as_root(monkeypatch):
    fake_pwd(monkeypatch, {"www-data": (33, 33)})

    assert resolve_owner("www-data", euid=0) == (33, 33)


def test_resolve_owner_as_the_target_user(monkeypatch):
    fake_pwd(monkeypatch, {"www-data": (33, 33)})

    assert resolve_owner("www-data", euid=33) == (33, 33)


def test_resolve_owner_cannot_chown_as_another_user(monkeypatch):
    fake_pwd(monkeypatch, {"www-data": (33, 33)})

    assert resolve_owner("www-data", euid=1000) is None


def test_resolve_owner_unknown_user(monkeypatch):
    fake_pwd(monkeypatch, {})

    assert resolve_owner("nobody-here", euid=0) is None
