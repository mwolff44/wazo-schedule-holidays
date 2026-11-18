# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+


class HolidaysError(Exception):
    """Base class of the errors raised by the service layer."""


class ConfigNotFound(HolidaysError):
    def __init__(self, schedule_id: int) -> None:
        super().__init__(f"no holidays configuration for schedule {schedule_id}")
        self.schedule_id = schedule_id


class InvalidConfig(HolidaysError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"field '{field}': {message}")
        self.field = field


class SyncInProgress(HolidaysError):
    def __init__(self, schedule_id: int) -> None:
        super().__init__(f"schedule {schedule_id} is being updated, retry later")
        self.schedule_id = schedule_id
