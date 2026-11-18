# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

"""Per-schedule JSON configuration (no database table, no migration).

One file ``<directory>/<schedule_id>.json`` per schedule::

    {"version": 1, "tenant_uuid": "...", "country": "NL", "subdivision": null,
     "excluded": ["Good Friday"],
     "managed": [{"date": "2027-03-29", "key": "Easter Monday"}]}

Writes are atomic (temporary file + ``os.replace``). ``lock`` serialises the
read-modify-write cycles of the confd process and of the cron CLI with
``flock``. The cron CLI runs as root while confd runs as ``www-data``: pass
``owner=(uid, gid)`` so that the files stay writable by confd.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .sync import Desired

CONFIG_VERSION = 1
FILE_MODE = 0o640
_CONFIG_FILE = re.compile(r"^(\d+)\.json$")


class StorageError(Exception):
    pass


class LockedError(StorageError):
    pass


@dataclass
class HolidayConfig:
    country: str
    subdivision: str | None = None
    excluded: list[str] = field(default_factory=list)
    managed: list[Desired] = field(default_factory=list)


def _check_id(schedule_id: object) -> int:
    if not isinstance(schedule_id, int) or isinstance(schedule_id, bool):
        raise TypeError(f"schedule id must be an integer: {schedule_id!r}")
    if schedule_id < 0:
        raise ValueError(f"invalid schedule id: {schedule_id}")
    return schedule_id


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _decode(raw: object) -> tuple[str, HolidayConfig]:
    if not isinstance(raw, dict):
        raise StorageError("configuration must be a JSON object")
    if raw.get("version") != CONFIG_VERSION:
        raise StorageError(f"unsupported version: {raw.get('version')!r}")
    tenant_uuid = raw.get("tenant_uuid")
    country = raw.get("country")
    subdivision = raw.get("subdivision")
    excluded = raw.get("excluded", [])
    managed_raw = raw.get("managed", [])
    if not isinstance(tenant_uuid, str) or not tenant_uuid:
        raise StorageError("missing tenant_uuid")
    if not isinstance(country, str) or not country:
        raise StorageError("missing country")
    if subdivision is not None and not isinstance(subdivision, str):
        raise StorageError("invalid subdivision")
    if not _is_str_list(excluded):
        raise StorageError("excluded must be a list of strings")
    if not isinstance(managed_raw, list):
        raise StorageError("managed must be a list")
    managed = []
    for entry in managed_raw:
        try:
            managed.append(
                Desired(
                    date=datetime.date.fromisoformat(entry["date"]),
                    key=_require_str(entry["key"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError(f"invalid managed entry: {entry!r}") from exc
    return tenant_uuid, HolidayConfig(
        country=country,
        subdivision=subdivision,
        excluded=list(excluded),
        managed=managed,
    )


def _require_str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(value)
    return value


def _encode(tenant_uuid: str, config: HolidayConfig) -> dict:
    return {
        "version": CONFIG_VERSION,
        "tenant_uuid": tenant_uuid,
        "country": config.country,
        "subdivision": config.subdivision,
        "excluded": list(config.excluded),
        "managed": [
            {"date": entry.date.isoformat(), "key": entry.key}
            for entry in config.managed
        ],
    }


class ConfigStorage:
    def __init__(
        self, directory: str | os.PathLike, owner: tuple[int, int] | None = None
    ) -> None:
        self.directory = Path(directory)
        self._owner = owner

    def path(self, schedule_id: int) -> Path:
        return self.directory / f"{_check_id(schedule_id)}.json"

    def _lock_path(self, schedule_id: int) -> Path:
        return self.directory / f"{_check_id(schedule_id)}.lock"

    def load_with_tenant(self, schedule_id: int) -> tuple[str, HolidayConfig] | None:
        try:
            text = self.path(schedule_id).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise StorageError(f"{self.path(schedule_id)}: invalid JSON") from exc
        return _decode(raw)

    def load(self, schedule_id: int, tenant_uuid: str) -> HolidayConfig | None:
        found = self.load_with_tenant(schedule_id)
        if found is None or found[0] != tenant_uuid:
            return None
        return found[1]

    def save(self, schedule_id: int, tenant_uuid: str, config: HolidayConfig) -> None:
        target = self.path(schedule_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_encode(tenant_uuid, config), indent=2, sort_keys=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.directory,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(handle.name, FILE_MODE)
            self._chown(handle.name)
            os.replace(handle.name, target)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(handle.name)
            raise

    def delete(self, schedule_id: int) -> None:
        for path in (self.path(schedule_id), self._lock_path(schedule_id)):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()

    def list_schedule_ids(self) -> list[int]:
        try:
            names = [entry.name for entry in self.directory.iterdir()]
        except FileNotFoundError:
            return []
        ids = []
        for name in names:
            match = _CONFIG_FILE.match(name)
            if match:
                ids.append(int(match.group(1)))
        return sorted(ids)

    def purge_orphans(self, existing_ids: Iterable[int]) -> list[int]:
        existing = set(existing_ids)
        removed = [sid for sid in self.list_schedule_ids() if sid not in existing]
        for schedule_id in removed:
            self.delete(schedule_id)
        return removed

    @contextlib.contextmanager
    def lock(self, schedule_id: int, blocking: bool = True) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._lock_path(schedule_id)
        with open(path, "a+") as handle:
            os.chmod(path, FILE_MODE)
            self._chown(path)
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(handle, flags)
            except BlockingIOError as exc:
                raise LockedError(f"schedule {schedule_id} is locked") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _chown(self, path: str | os.PathLike) -> None:
        if self._owner is not None:
            os.chown(path, *self._owner)
