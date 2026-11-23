# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import datetime
import json
import os
import stat

import pytest

from wazo_schedule_holidays import storage as storage_module
from wazo_schedule_holidays.storage import (
    ConfigStorage,
    HolidayConfig,
    LockedError,
    StorageError,
)
from wazo_schedule_holidays.sync import Closure, Desired

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def storage(tmp_path):
    return ConfigStorage(tmp_path / "holidays")


def config(**kwargs):
    defaults = {
        "country": "NL",
        "subdivision": None,
        "excluded": ["Good Friday"],
        "managed": [Desired(date=datetime.date(2027, 3, 29), key="Easter Monday")],
    }
    return HolidayConfig(**{**defaults, **kwargs})


def write_raw(storage, schedule_id, content):
    storage.directory.mkdir(parents=True, exist_ok=True)
    storage.path(schedule_id).write_text(
        content if isinstance(content, str) else json.dumps(content)
    )


def test_missing_config_is_none(storage):
    assert storage.load(1, TENANT) is None


def test_save_then_load_roundtrip(storage):
    storage.save(1, TENANT, config(subdivision="6AE"))

    assert storage.load(1, TENANT) == config(subdivision="6AE")


def test_saved_file_format(storage):
    storage.save(7, TENANT, config())

    assert json.loads(storage.path(7).read_text()) == {
        "version": 1,
        "tenant_uuid": TENANT,
        "country": "NL",
        "subdivision": None,
        "excluded": ["Good Friday"],
        "closures": [],
        "managed": [
            {
                "date": "2027-03-29",
                "key": "Easter Monday",
                "hours_start": "00:00",
                "hours_end": "23:59",
            }
        ],
    }


def test_non_ascii_label_is_written_as_utf8(storage):
    seminar = Closure(
        start=datetime.datetime(2027, 11, 19, 12, 0),
        end=datetime.datetime(2027, 11, 20, 14, 0),
        label="Séminaire",
        id="abc",
    )
    storage.save(1, TENANT, config(closures=[seminar]))

    assert "Séminaire" in storage.path(1).read_text(encoding="utf-8")
    assert storage.load(1, TENANT).closures == [seminar]


def test_closures_and_partial_hours_roundtrip(storage):
    seminar = Closure(
        start=datetime.datetime(2027, 11, 19, 12, 0),
        end=datetime.datetime(2027, 11, 20, 14, 0),
        label="Seminar",
        id="abc",
    )
    managed = [
        Desired(datetime.date(2027, 11, 19), "Seminar", "12:00", "23:59", "abc"),
        Desired(datetime.date(2027, 11, 20), "Seminar", "00:00", "14:00", "abc"),
    ]
    storage.save(1, TENANT, config(closures=[seminar], managed=managed))

    loaded = storage.load(1, TENANT)

    assert loaded.closures == [seminar]
    assert loaded.managed == managed
    raw = json.loads(storage.path(1).read_text())
    assert raw["closures"] == [
        {
            "id": "abc",
            "start": "2027-11-19T12:00",
            "end": "2027-11-20T14:00",
            "label": "Seminar",
        }
    ]
    assert raw["managed"][0]["closure_id"] == "abc"


def test_file_written_before_closures_existed_is_still_valid(storage):
    write_raw(
        storage,
        1,
        {
            "version": 1,
            "tenant_uuid": TENANT,
            "country": "NL",
            "managed": [{"date": "2027-03-29", "key": "Easter Monday"}],
        },
    )

    loaded = storage.load(1, TENANT)

    assert loaded.closures == []
    assert loaded.managed == [Desired(datetime.date(2027, 3, 29), "Easter Monday")]


@pytest.mark.parametrize(
    "closures",
    [
        "nope",
        [{"start": "2027-11-19T12:00"}],
        [{"start": "bad", "end": "2027-11-20T14:00"}],
        [{"start": "2027-11-19T12:00", "end": "2027-11-20T14:00", "label": 3}],
        ["x"],
    ],
)
def test_invalid_closures_are_rejected(storage, closures):
    write_raw(
        storage,
        1,
        {"version": 1, "tenant_uuid": TENANT, "country": "NL", "closures": closures},
    )

    with pytest.raises(StorageError):
        storage.load(1, TENANT)


def test_load_for_another_tenant_is_none(storage):
    storage.save(1, TENANT, config())

    assert storage.load(1, OTHER_TENANT) is None


def test_load_with_tenant_returns_the_owner_tenant(storage):
    storage.save(1, TENANT, config())

    assert storage.load_with_tenant(1) == (TENANT, config())
    assert storage.load_with_tenant(2) is None


def test_save_overwrites(storage):
    storage.save(1, TENANT, config())
    storage.save(1, TENANT, config(country="FR", excluded=[], managed=[]))

    assert storage.load(1, TENANT).country == "FR"


def test_file_mode_is_not_world_readable(storage):
    storage.save(1, TENANT, config())

    mode = stat.S_IMODE(os.stat(storage.path(1)).st_mode)
    assert mode == 0o640


def test_owner_is_applied_to_the_file(storage, monkeypatch):
    calls = []
    monkeypatch.setattr(storage_module.os, "chown", lambda *args: calls.append(args))
    owned = ConfigStorage(storage.directory, owner=(33, 33))

    owned.save(1, TENANT, config())

    assert calls and calls[0][1:] == (33, 33)


def test_failed_write_keeps_previous_content_and_leaves_no_temp_file(
    storage, monkeypatch
):
    storage.save(1, TENANT, config())

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(storage_module.os, "replace", boom)
    with pytest.raises(OSError):
        storage.save(1, TENANT, config(country="FR"))
    monkeypatch.undo()

    assert storage.load(1, TENANT).country == "NL"
    assert sorted(p.name for p in storage.directory.iterdir()) == ["1.json"]


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        {"version": 2, "tenant_uuid": TENANT, "country": "NL"},
        {"tenant_uuid": TENANT, "country": "NL"},
        {"version": 1, "country": "NL"},
        {"version": 1, "tenant_uuid": TENANT, "country": ""},
        {"version": 1, "tenant_uuid": TENANT, "country": 3},
        {"version": 1, "tenant_uuid": TENANT, "country": "NL", "excluded": "x"},
        {"version": 1, "tenant_uuid": TENANT, "country": "NL", "managed": [{}]},
        {
            "version": 1,
            "tenant_uuid": TENANT,
            "country": "NL",
            "managed": [{"date": "2027-13-40", "key": "x"}],
        },
    ],
)
def test_corrupted_files_raise_a_storage_error(storage, content):
    write_raw(storage, 1, content)

    with pytest.raises(StorageError):
        storage.load(1, TENANT)


def test_minimal_valid_file_gets_defaults(storage):
    write_raw(storage, 1, {"version": 1, "tenant_uuid": TENANT, "country": "FR"})

    assert storage.load(1, TENANT) == HolidayConfig(country="FR")


def test_delete_is_idempotent(storage):
    storage.save(1, TENANT, config())

    storage.delete(1)
    storage.delete(1)

    assert storage.load(1, TENANT) is None


def test_delete_also_removes_the_lock_file(storage):
    storage.save(1, TENANT, config())
    with storage.lock(1):
        pass

    storage.delete(1)

    assert list(storage.directory.iterdir()) == []


def test_list_schedule_ids_ignores_foreign_files(storage):
    storage.save(3, TENANT, config())
    storage.save(1, TENANT, config())
    for name in ("abc.json", "2.json.tmp", "2.lock", "notes.txt"):
        (storage.directory / name).write_text("")

    assert storage.list_schedule_ids() == [1, 3]


def test_list_schedule_ids_without_directory(storage):
    assert storage.list_schedule_ids() == []


def test_purge_orphans_removes_configs_of_deleted_schedules(storage):
    for schedule_id in (1, 2, 3):
        storage.save(schedule_id, TENANT, config())

    removed = storage.purge_orphans(existing_ids=[1, 3])

    assert removed == [2]
    assert storage.list_schedule_ids() == [1, 3]


@pytest.mark.parametrize("bad_id", ["1/../2", "../x", "", None, 1.5])
def test_schedule_id_cannot_escape_the_directory(storage, bad_id):
    with pytest.raises((ValueError, TypeError)):
        storage.path(bad_id)


def test_lock_excludes_a_second_holder(storage):
    other = ConfigStorage(storage.directory)

    with storage.lock(1):
        with pytest.raises(LockedError):
            with other.lock(1, blocking=False):
                pass
        with other.lock(2, blocking=False):
            pass


def test_lock_is_released_on_exit_and_on_error(storage):
    other = ConfigStorage(storage.directory)

    with pytest.raises(RuntimeError):
        with storage.lock(1):
            raise RuntimeError

    with other.lock(1, blocking=False):
        pass


def test_a_config_without_country_holds_closures_only(storage):
    seminar = Closure(
        datetime.datetime(2027, 11, 19, 12), datetime.datetime(2027, 11, 20, 14), id="a"
    )
    storage.save(1, TENANT, HolidayConfig(closures=[seminar]))

    loaded = storage.load(1, TENANT)

    assert loaded.country is None and loaded.closures == [seminar]


def test_a_closure_without_id_gets_a_stable_one(storage):
    entry = {"start": "2027-11-19T12:00", "end": "2027-11-20T14:00", "label": "S"}
    write_raw(storage, 1, {"version": 1, "tenant_uuid": TENANT, "closures": [entry]})

    first = storage.load(1, TENANT).closures[0].id
    second = storage.load(1, TENANT).closures[0].id

    assert first and first == second
