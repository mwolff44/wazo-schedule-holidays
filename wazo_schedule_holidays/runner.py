# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

"""Daily synchronisation of every configured schedule (used by the cron CLI).

Wazo-free on purpose: the database scope, the bus flush/rollback and the "not
found" exception class are injected by ``cli.py``. Each schedule is synchronised
in its own transaction so that one failure does not prevent the others.
"""

from __future__ import annotations

import logging
import pwd
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

from .exceptions import ConfigNotFound, SyncInProgress
from .service import SyncResult
from .storage import ConfigStorage, StorageError

logger = logging.getLogger(__name__)


@dataclass
class Report:
    results: dict[int, SyncResult] = field(default_factory=dict)
    failed: dict[int, str] = field(default_factory=dict)
    orphans: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        changed = sum(1 for result in self.results.values() if result.changed)
        return (
            f"{len(self.results)} synchronised ({changed} changed), "
            f"{len(self.failed)} failed, {len(self.orphans)} orphaned"
        )


def resolve_owner(user_name: str, euid: int) -> tuple[int, int] | None:
    """(uid, gid) to give to the files, if this process is allowed to chown.

    confd runs as ``user_name`` (www-data) while the cron job runs as root.
    """
    try:
        entry = pwd.getpwnam(user_name)
    except KeyError:
        logger.warning("unknown user '%s': files will keep their owner", user_name)
        return None
    if euid not in (0, entry.pw_uid):
        return None
    return (entry.pw_uid, entry.pw_gid)


def sync_all(
    service: Any,
    storage: ConfigStorage,
    scope: Callable[[], AbstractContextManager],
    flush: Callable[[], None],
    rollback: Callable[[], None],
    not_found: tuple[type[BaseException], ...],
    dry_run: bool = False,
    schedule_id: int | None = None,
) -> Report:
    report = Report()
    ids = [schedule_id] if schedule_id is not None else storage.list_schedule_ids()
    for current in ids:
        try:
            found = storage.load_with_tenant(current)
        except StorageError as exc:
            logger.error("schedule %s: %s", current, exc)
            report.failed[current] = str(exc)
            continue
        if found is None:
            report.failed[current] = f"no holidays configuration for schedule {current}"
            logger.error("schedule %s: %s", current, report.failed[current])
            continue
        tenant_uuid, _ = found
        try:
            with scope():
                result = service.sync(current, [tenant_uuid], dry_run=dry_run)
            flush()
        except not_found:
            logger.warning("schedule %s no longer exists: dropping its config", current)
            report.orphans.append(current)
            if not dry_run:
                storage.delete(current)
        except ConfigNotFound:
            logger.info("schedule %s: configuration removed meanwhile", current)
        except SyncInProgress as exc:
            rollback()
            logger.warning("schedule %s: %s", current, exc)
            report.failed[current] = str(exc)
        except Exception as exc:
            rollback()
            logger.exception("schedule %s: synchronisation failed", current)
            report.failed[current] = str(exc)
        else:
            report.results[current] = result
            logger.info(
                "schedule %s: %d added, %d removed%s",
                current,
                len(result.added),
                len(result.removed),
                " (dry run)" if dry_run else "",
            )
    return report
