# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

from wazo_confd.plugins.schedule.service import build_service as build_schedule_service
from xivo_dao.alchemy.schedule_time import ScheduleTime
from xivo_dao.helpers.db_manager import Session
from xivo_dao.resources.infos import dao as infos_dao

from .resources import (
    CountriesResource,
    CountryHolidaysResource,
    ScheduleHolidaysResource,
    ScheduleHolidaysSyncResource,
)
from .service import HolidaysService
from .storage import ConfigStorage

DEFAULT_DIRECTORY = "/var/lib/wazo-schedule-holidays"


def _default_timezone():
    return infos_dao.get().timezone


class Plugin:
    def load(self, dependencies):
        api = dependencies["api"]
        config = dependencies["config"]

        settings = config.get("schedule_holidays") or {}
        storage = ConfigStorage(settings.get("directory", DEFAULT_DIRECTORY))
        service = HolidaysService(
            build_schedule_service(),
            storage,
            ScheduleTime,
            default_timezone=_default_timezone,
            commit=Session.commit,
        )

        api.add_resource(CountriesResource, "/schedule-holidays/countries")
        api.add_resource(
            CountryHolidaysResource, "/schedule-holidays/countries/<string:code>"
        )
        api.add_resource(
            ScheduleHolidaysResource,
            "/schedules/<int:id>/holidays",
            resource_class_args=(service,),
        )
        api.add_resource(
            ScheduleHolidaysSyncResource,
            "/schedules/<int:id>/holidays/sync",
            resource_class_args=(service,),
        )
