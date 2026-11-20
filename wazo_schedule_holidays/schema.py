# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

from marshmallow import fields
from marshmallow.validate import Length, Range
from wazo_confd.helpers.mallow import BaseSchema


class ManagedHolidaySchema(BaseSchema):
    date = fields.Date(dump_only=True)
    key = fields.String(dump_only=True)
    hours_start = fields.String(dump_only=True)
    hours_end = fields.String(dump_only=True)


class RemovedDaySchema(BaseSchema):
    month = fields.Integer(dump_only=True)
    day = fields.Integer(dump_only=True)
    hours_start = fields.String(dump_only=True)
    hours_end = fields.String(dump_only=True)


class ClosureSchema(BaseSchema):
    # Naive local date-times (schedule timezone), minute precision.
    start = fields.DateTime(format="%Y-%m-%dT%H:%M", required=True)
    end = fields.DateTime(format="%Y-%m-%dT%H:%M", required=True)
    label = fields.String(
        allow_none=True, load_default=None, validate=Length(min=1, max=128)
    )


class ScheduleHolidaysSchema(BaseSchema):
    country = fields.String(required=True, validate=Length(min=2, max=3))
    subdivision = fields.String(
        allow_none=True, load_default=None, validate=Length(min=1, max=32)
    )
    excluded = fields.List(
        fields.String(validate=Length(min=1, max=255)), load_default=list
    )
    closures = fields.List(
        fields.Nested(ClosureSchema), load_default=list, validate=Length(max=100)
    )
    managed = fields.List(fields.Nested(ManagedHolidaySchema), dump_only=True)


class ScheduleHolidaysSyncQuerySchema(BaseSchema):
    dry_run = fields.Boolean(load_default=False)


class ScheduleHolidaysSyncSchema(BaseSchema):
    dry_run = fields.Boolean(dump_only=True)
    changed = fields.Boolean(dump_only=True)
    today = fields.Date(dump_only=True)
    added = fields.List(fields.Nested(ManagedHolidaySchema), dump_only=True)
    removed = fields.List(fields.Nested(RemovedDaySchema), dump_only=True)
    managed = fields.List(fields.Nested(ManagedHolidaySchema), dump_only=True)


class HolidaySchema(BaseSchema):
    key = fields.String(dump_only=True)
    name = fields.String(dump_only=True)
    date = fields.Date(dump_only=True)


class HolidaysQuerySchema(BaseSchema):
    subdivision = fields.String(validate=Length(min=1, max=32))
    year = fields.Integer(validate=Range(min=1900, max=2100))
    language = fields.String(load_default="en_US", validate=Length(min=2, max=16))
