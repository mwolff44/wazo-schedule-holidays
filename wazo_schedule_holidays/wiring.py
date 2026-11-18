# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

"""Builds the holidays service on top of wazo-confd / xivo-dao.

Shared by the confd plugin and the cron CLI so both behave the same way.
"""

from xivo_dao.alchemy.schedule_time import ScheduleTime
from xivo_dao.helpers.db_manager import Session
from xivo_dao.resources.infos import dao as infos_dao

from .service import HolidaysService
from .storage import DEFAULT_DIRECTORY, ConfigStorage


def default_timezone():
    return infos_dao.get().timezone


def build_holidays_service(config, schedule_service, owner=None):
    """Return ``(service, storage)``; ``owner`` is an optional ``(uid, gid)``."""
    settings = config.get("schedule_holidays") or {}
    storage = ConfigStorage(settings.get("directory", DEFAULT_DIRECTORY), owner=owner)
    service = HolidaysService(
        schedule_service,
        storage,
        ScheduleTime,
        default_timezone=default_timezone,
        commit=Session.commit,
    )
    return service, storage
