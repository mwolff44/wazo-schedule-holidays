# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import datetime
from functools import wraps

import pycountry
from flask import request
from wazo_confd.auth import required_acl
from wazo_confd.helpers.restful import ConfdResource
from xivo.rest_api_helpers import APIException
from xivo_dao.helpers import errors
from xivo_dao.helpers.exception import InputError

from . import holiday_calendar
from .exceptions import (
    ClosureNotFound,
    ConfigNotFound,
    InvalidConfig,
    SyncInProgress,
)
from .holiday_calendar import UnknownCountry, UnknownSubdivision
from .schema import (
    ClosureListSchema,
    ClosureSchema,
    HolidaySchema,
    HolidaysQuerySchema,
    ScheduleHolidaysSchema,
    ScheduleHolidaysSyncQuerySchema,
    ScheduleHolidaysSyncSchema,
)
from .storage import StorageError


def _input_error(message):
    return InputError(errors.format_error(InputError.prefix, message))


def translate_errors(func):
    """Map the plugin errors to the ones wazo-confd turns into HTTP responses."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ConfigNotFound as exc:
            raise errors.not_found("ScheduleHolidays", schedule_id=exc.schedule_id)
        except ClosureNotFound as exc:
            raise errors.not_found(
                "ScheduleClosure",
                schedule_id=exc.schedule_id,
                closure_id=exc.closure_id,
            )
        except (InvalidConfig, UnknownCountry, UnknownSubdivision) as exc:
            raise _input_error(str(exc))
        except SyncInProgress as exc:
            raise APIException(409, str(exc), "schedule-holidays-busy")
        except StorageError as exc:
            raise APIException(500, str(exc), "schedule-holidays-storage")

    return wrapper


def _country_name(code):
    country = pycountry.countries.get(alpha_2=code)
    return country.name if country else code


class CountriesResource(ConfdResource):
    @required_acl("confd.schedule_holidays.read")
    def get(self):
        items = [
            {
                "code": code,
                "name": _country_name(code),
                "subdivisions": [
                    {"code": sub_code, "name": name}
                    for sub_code, name in holiday_calendar.subdivision_names(
                        code
                    ).items()
                ],
            }
            for code in holiday_calendar.list_countries()
        ]
        return {"total": len(items), "items": items}


class CountryHolidaysResource(ConfdResource):
    @required_acl("confd.schedule_holidays.read")
    @translate_errors
    def get(self, code):
        query = HolidaysQuerySchema().load(request.args)
        year = query.get("year", datetime.date.today().year)
        try:
            holidays = holiday_calendar.holidays_for(
                code, query.get("subdivision"), year, query["language"]
            )
        except UnknownCountry:
            raise errors.not_found("Country", code=code)
        return {
            "total": len(holidays),
            "items": HolidaySchema().dump(holidays, many=True),
        }


class _ScheduleResource(ConfdResource):
    has_tenant_uuid = True

    def __init__(self, service):
        super().__init__()
        self.service = service

    def _tenant_uuids(self):
        return self._build_tenant_list({"recurse": True})


class ScheduleHolidaysResource(_ScheduleResource):
    @required_acl("confd.schedules.{id}.holidays.read")
    @translate_errors
    def get(self, id):
        config = self.service.get_config(id, self._tenant_uuids())
        return ScheduleHolidaysSchema().dump(config)

    @required_acl("confd.schedules.{id}.holidays.update")
    @translate_errors
    def put(self, id):
        form = ScheduleHolidaysSchema().load(request.get_json(force=True))
        self.service.update_config(
            id,
            self._tenant_uuids(),
            form["country"],
            form["subdivision"],
            form["excluded"],
        )
        return "", 204

    @required_acl("confd.schedules.{id}.holidays.delete")
    @translate_errors
    def delete(self, id):
        self.service.delete_config(id, self._tenant_uuids())
        return "", 204


class _SyncResource(_ScheduleResource):
    def _sync(self, id):
        query = ScheduleHolidaysSyncQuerySchema().load(request.args)
        result = self.service.sync(id, self._tenant_uuids(), dry_run=query["dry_run"])
        payload = {
            "dry_run": result.dry_run,
            "changed": result.changed,
            "today": result.today,
            "added": result.added,
            "removed": [
                {"month": m, "day": d, "hours_start": hs, "hours_end": he}
                for m, d, hs, he in result.removed
            ],
            "managed": result.managed,
        }
        return ScheduleHolidaysSyncSchema().dump(payload)


class ScheduleExceptionalPeriodsSyncResource(_SyncResource):
    @required_acl("confd.schedules.{id}.exceptional_periods.sync.create")
    @translate_errors
    def post(self, id):
        return self._sync(id)


class ScheduleHolidaysSyncResource(_SyncResource):
    """Former route of the synchronisation, kept as an alias."""

    @required_acl("confd.schedules.{id}.holidays.sync.create")
    @translate_errors
    def post(self, id):
        return self._sync(id)


class ScheduleClosuresResource(_ScheduleResource):
    @required_acl("confd.schedules.{id}.closures.read")
    @translate_errors
    def get(self, id):
        items = self.service.list_closures(id, self._tenant_uuids())
        return ClosureListSchema().dump({"total": len(items), "items": items})

    @required_acl("confd.schedules.{id}.closures.create")
    @translate_errors
    def post(self, id):
        form = ClosureSchema().load(request.get_json(force=True))
        closure = self.service.create_closure(
            id, self._tenant_uuids(), form["start"], form["end"], form["label"]
        )
        return ClosureSchema().dump(closure), 201


class ScheduleClosureResource(_ScheduleResource):
    @required_acl("confd.schedules.{id}.closures.{closure_id}.read")
    @translate_errors
    def get(self, id, closure_id):
        closure = self.service.get_closure(id, self._tenant_uuids(), closure_id)
        return ClosureSchema().dump(closure)

    @required_acl("confd.schedules.{id}.closures.{closure_id}.update")
    @translate_errors
    def put(self, id, closure_id):
        form = ClosureSchema().load(request.get_json(force=True))
        self.service.update_closure(
            id,
            self._tenant_uuids(),
            closure_id,
            form["start"],
            form["end"],
            form["label"],
        )
        return "", 204

    @required_acl("confd.schedules.{id}.closures.{closure_id}.delete")
    @translate_errors
    def delete(self, id, closure_id):
        self.service.delete_closure(id, self._tenant_uuids(), closure_id)
        return "", 204
