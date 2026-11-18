# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

from wazo_confd.plugins.schedule.service import build_service as build_schedule_service

from .resources import (
    CountriesResource,
    CountryHolidaysResource,
    ScheduleHolidaysResource,
    ScheduleHolidaysSyncResource,
)
from .wiring import build_holidays_service


class Plugin:
    def load(self, dependencies):
        api = dependencies["api"]
        config = dependencies["config"]

        service, _ = build_holidays_service(config, build_schedule_service())

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
