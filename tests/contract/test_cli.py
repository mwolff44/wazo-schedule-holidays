# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import contextlib
from types import SimpleNamespace

import pytest
from wazo_confd.config import DEFAULT_CONFIG
from xivo_dao.alchemy.schedule import Schedule
from xivo_dao.helpers import errors

from wazo_schedule_holidays import cli
from wazo_schedule_holidays.storage import ConfigStorage, HolidayConfig

TENANT = "11111111-1111-1111-1111-111111111111"
REAL_LOAD_CONFIG = cli.load_config  # Env replaces it by a dict, keep the real one


class FakeBus:
    instances: list = []

    def __init__(self):
        self.queued = []
        self.published = []
        FakeBus.instances.append(self)

    @classmethod
    def from_config(cls, service_uuid, bus_config):
        return cls()

    def queue_event(self, event, *, extra_headers=None):
        self.queued.append(event)

    def flush(self):
        self.published.extend(self.queued)
        self.queued.clear()

    def rollback(self):
        self.queued.clear()


class FakeScheduleDao:
    def __init__(self, *schedules):
        self.schedules = {schedule.id: schedule for schedule in schedules}
        self.edited = []
        self.fail_edit = None

    def get(self, schedule_id, tenant_uuids=None):
        schedule = self.schedules.get(schedule_id)
        if schedule is None or (
            tenant_uuids is not None and schedule.tenant_uuid not in tenant_uuids
        ):
            raise errors.not_found("Schedule", id=schedule_id)
        return schedule

    def edit(self, schedule):
        if self.fail_edit:
            raise self.fail_edit
        self.edited.append(schedule.id)


class Env:
    def __init__(self, tmp_path, monkeypatch):
        FakeBus.instances.clear()
        self.directory = tmp_path / "holidays"
        self.storage = ConfigStorage(self.directory)
        self.schedule = Schedule(
            id=1,
            tenant_uuid=TENANT,
            timezone="Europe/Amsterdam",
            fallback_action="voicemail",
            fallback_actionid="12",
            fallback_actionargs=None,
        )
        self.dao = FakeScheduleDao(self.schedule)
        config = {
            "user": "no-such-user-here",
            "bus": {},
            "schedule_holidays": {"directory": str(self.directory)},
        }
        monkeypatch.setattr(cli, "load_config", lambda: config)
        monkeypatch.setattr(cli.xivo_logging, "setup_logging", lambda *a, **k: None)
        monkeypatch.setattr(cli, "init_db_from_config", lambda config: None)
        monkeypatch.setattr(
            cli, "info_dao", SimpleNamespace(get=lambda: SimpleNamespace(uuid="uuid"))
        )
        monkeypatch.setattr(cli, "BusPublisher", FakeBus)
        monkeypatch.setattr(cli, "schedule_dao", self.dao)
        monkeypatch.setattr(cli, "session_scope", contextlib.nullcontext)
        self.storage.save(1, TENANT, HolidayConfig(country="NL"))

    @property
    def bus(self):
        return FakeBus.instances[-1]

    def days(self):
        return sorted(
            (p.months_list[0], p.month_days[0])
            for p in self.schedule.exceptional_periods
        )


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def test_cli_args_defaults_and_flags():
    args = cli.parse_cli_args([])
    assert not args.debug and not args.quiet and not args.dry_run
    assert args.schedule_id is None

    args = cli.parse_cli_args(["-q", "-n", "-s", "4"])
    assert args.quiet and args.dry_run and args.schedule_id == 4


def test_run_creates_periods_and_publishes_the_schedule_edited_event(env):
    assert cli.main(["--quiet"]) == 0

    assert (12, 25) in env.days()
    assert env.dao.edited == [1]
    assert [type(e).__name__ for e in env.bus.published] == ["ScheduleEditedEvent"]
    assert env.storage.load(1, TENANT).managed


def test_second_run_changes_nothing_and_publishes_nothing(env):
    cli.main(["--quiet"])
    before = env.days()

    assert cli.main(["--quiet"]) == 0

    assert env.days() == before
    assert env.dao.edited == [1]
    assert env.bus.published == []


def test_dry_run_changes_nothing(env):
    assert cli.main(["--quiet", "--dry-run"]) == 0

    assert env.schedule.exceptional_periods == []
    assert env.dao.edited == []
    assert env.bus.published == []


def test_failure_exits_with_1_and_publishes_no_event(env):
    env.dao.fail_edit = RuntimeError("db down")

    assert cli.main(["--quiet"]) == 1

    assert env.bus.published == []
    assert env.bus.queued == []


def test_configuration_of_a_deleted_schedule_is_removed(env):
    env.storage.save(5, TENANT, HolidayConfig(country="NL"))

    assert cli.main(["--quiet"]) == 0

    assert env.storage.list_schedule_ids() == [1]


def test_only_the_requested_schedule_is_synced(env):
    other = Schedule(
        id=2,
        tenant_uuid=TENANT,
        timezone="Europe/Amsterdam",
        fallback_action="voicemail",
        fallback_actionid="12",
        fallback_actionargs=None,
    )
    env.dao.schedules[2] = other
    env.storage.save(2, TENANT, HolidayConfig(country="NL"))

    assert cli.main(["--quiet", "--schedule-id", "2"]) == 0

    assert env.dao.edited == [2]
    assert env.schedule.exceptional_periods == []


def write_config_files(tmp_path, monkeypatch, directory):
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir(parents=True)
    (tmp_path / "config.yml").write_text("bus:\n  host: bus.example.com\n")
    (conf_d / "schedule-holidays.yml").write_text(
        f"schedule_holidays:\n  directory: {directory}\n"
    )
    monkeypatch.setattr(
        cli,
        "DEFAULT_CONFIG",
        {
            **DEFAULT_CONFIG,
            "config_file": str(tmp_path / "config.yml"),
            "extra_config_files": str(conf_d),
        },
    )


def test_load_config_keeps_the_defaults_and_applies_the_config_files(
    tmp_path, monkeypatch
):
    write_config_files(tmp_path, monkeypatch, "/somewhere")

    config = REAL_LOAD_CONFIG()

    assert config["user"] == "www-data"
    assert (
        config["rest_api"]["min_threads"] == DEFAULT_CONFIG["rest_api"]["min_threads"]
    )
    assert config["bus"]["host"] == "bus.example.com"
    assert config["bus"]["username"] == "guest"
    assert config["schedule_holidays"]["directory"] == "/somewhere"


def test_run_with_the_real_load_config(env, tmp_path, monkeypatch):
    write_config_files(tmp_path / "etc", monkeypatch, env.directory)
    monkeypatch.setattr(cli, "load_config", REAL_LOAD_CONFIG)

    assert cli.main(["--quiet"]) == 0

    assert (12, 25) in env.days()
