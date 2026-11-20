# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import datetime

import pytest
from flask import Flask, g
from flask_restful import Api
from xivo_dao.alchemy.schedule import Schedule
from xivo_dao.alchemy.schedule_time import ScheduleTime
from xivo_dao.helpers import errors

from wazo_schedule_holidays import plugin as plugin_module
from wazo_schedule_holidays.storage import ConfigStorage

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
HEADERS = {"X-Auth-Token": "token"}


class FakeToken:
    def __init__(self, auth):
        self._auth = auth

    def check(self, token_uuid, required_acl, tenant=None):
        self._auth.acls.append(required_acl)
        return True

    def get(self, token_uuid):
        return {"metadata": {"tenant_uuid": TENANT}}

    def is_valid(self, token_uuid, tenant=None):
        return True


class FakeTenants:
    def list(self, tenant_uuid=None):
        return {"items": [{"uuid": TENANT, "name": "tenant"}]}


class FakeAuth:
    host = "auth"
    port = 9497

    def __init__(self):
        self.acls = []
        self.token = FakeToken(self)
        self.tenants = FakeTenants()

    def set_token(self, token):
        pass


class FakeScheduleService:
    """Stands for CRUDService(schedule_dao, ...): real Schedule objects, no DB."""

    def __init__(self, *schedules):
        self.schedules = {schedule.id: schedule for schedule in schedules}
        self.edits = []

    def get(self, resource_id, tenant_uuids=None):
        schedule = self.schedules.get(resource_id)
        if schedule is None or (
            tenant_uuids is not None and schedule.tenant_uuid not in tenant_uuids
        ):
            raise errors.not_found("Schedule", id=resource_id)
        return schedule

    def edit(self, schedule, updated_fields=None):
        self.edits.append((schedule.id, updated_fields))


def make_schedule(schedule_id=1, tenant_uuid=TENANT):
    return Schedule(
        id=schedule_id,
        tenant_uuid=tenant_uuid,
        timezone="Europe/Amsterdam",
        fallback_action="voicemail",
        fallback_actionid="12",
        fallback_actionargs=None,
    )


class Harness:
    def __init__(self, tmp_path, monkeypatch):
        self.auth = FakeAuth()
        self.schedule = make_schedule()
        self.foreign = make_schedule(2, OTHER_TENANT)
        self.schedules = FakeScheduleService(self.schedule, self.foreign)
        monkeypatch.setattr(
            plugin_module, "build_schedule_service", lambda: self.schedules
        )
        app = Flask(__name__)
        app.testing = True

        @app.before_request
        def inject_auth():
            g.auth_client = self.auth

        api = Api(app)
        self.directory = tmp_path / "holidays"
        plugin_module.Plugin().load(
            {
                "api": api,
                "config": {"schedule_holidays": {"directory": str(self.directory)}},
            }
        )
        self.client = app.test_client()
        self.storage = ConfigStorage(self.directory)

    def get(self, url):
        return self.client.get(url, headers=HEADERS)

    def put(self, url, body):
        return self.client.put(url, json=body, headers=HEADERS)

    def post(self, url, body=None):
        return self.client.post(url, json=body, headers=HEADERS)

    def delete(self, url):
        return self.client.delete(url, headers=HEADERS)


@pytest.fixture
def http(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def days(schedule):
    return sorted(
        (p.months_list[0], p.month_days[0]) for p in schedule.exceptional_periods
    )


def test_countries(http):
    response = http.get("/schedule-holidays/countries")

    assert response.status_code == 200
    body = response.get_json()
    assert body["total"] == len(body["items"])
    by_code = {item["code"]: item for item in body["items"]}
    assert by_code["NL"]["name"] == "Netherlands"
    fr = {sub["code"]: sub["name"] for sub in by_code["FR"]["subdivisions"]}
    assert fr["6AE"] == "Alsace" and fr["57"] == "Moselle"
    assert "Zone A" not in fr
    assert http.auth.acls == ["confd.schedule_holidays.read"]


def test_country_holidays_in_dutch(http):
    response = http.get("/schedule-holidays/countries/NL?year=2027&language=nl")

    assert response.status_code == 200
    items = {item["key"]: item for item in response.get_json()["items"]}
    assert items["Easter Monday"] == {
        "key": "Easter Monday",
        "name": "Tweede paasdag",
        "date": "2027-03-29",
    }


def test_country_holidays_alsace_has_good_friday(http):
    response = http.get("/schedule-holidays/countries/FR?year=2027&subdivision=6AE")

    keys = [item["key"] for item in response.get_json()["items"]]
    assert "Good Friday" in keys


def test_country_holidays_errors(http):
    assert http.get("/schedule-holidays/countries/ZZ").status_code == 404
    assert http.get("/schedule-holidays/countries/FR?subdivision=67").status_code == 400
    assert http.get("/schedule-holidays/countries/FR?year=abc").status_code == 400


def test_config_is_not_found_before_it_is_created(http):
    response = http.get("/schedules/1/holidays")

    assert response.status_code == 404
    assert http.auth.acls == ["confd.schedules.1.holidays.read"]


def test_put_then_get_config(http):
    put = http.put(
        "/schedules/1/holidays",
        {"country": "FR", "subdivision": "6AE", "excluded": ["Good Friday"]},
    )
    get = http.get("/schedules/1/holidays")

    assert put.status_code == 204
    assert get.status_code == 200
    assert get.get_json() == {
        "country": "FR",
        "subdivision": "6AE",
        "excluded": ["Good Friday"],
        "managed": [],
    }
    assert http.auth.acls == [
        "confd.schedules.1.holidays.update",
        "confd.schedules.1.holidays.read",
    ]


def seminar_body():
    start = datetime.date.today() + datetime.timedelta(days=10)
    end = start + datetime.timedelta(days=1)
    body = {
        "start": f"{start.isoformat()}T12:00",
        "end": f"{end.isoformat()}T14:00",
        "label": "Seminar",
    }
    return body, start, end


def test_post_closure_synchronises_the_schedule_at_once(http):
    body, start, end = seminar_body()

    response = http.post("/schedules/1/closures", body)

    assert response.status_code == 201
    created = response.get_json()
    assert created["id"] and created["start"] == body["start"]
    assert created["label"] == "Seminar"
    assert http.auth.acls == ["confd.schedules.1.closures.create"]
    hours = {
        (p.months_list[0], p.month_days[0]): p.hours
        for p in http.schedule.exceptional_periods
    }
    assert hours == {
        (start.month, start.day): "12:00-23:59",
        (end.month, end.day): "00:00-14:00",
    }
    for period in http.schedule.exceptional_periods:
        assert isinstance(period, ScheduleTime) and period.mode == "closed"
        assert (period.action, period.actionid) == ("voicemail", "12")
    # a closure needs no holidays configuration
    assert http.get("/schedules/1/holidays").status_code == 404


def test_list_get_put_delete_closure(http):
    body, start, _ = seminar_body()
    created = http.post("/schedules/1/closures", body).get_json()
    url = f"/schedules/1/closures/{created['id']}"

    listing = http.get("/schedules/1/closures").get_json()
    one = http.get(url)
    later = {**body, "start": f"{start.isoformat()}T08:00", "label": None}
    put = http.put(url, later)
    after = http.get(url).get_json()
    deleted = http.delete(url)

    assert listing == {"total": 1, "items": [created]}
    assert one.get_json() == created
    assert put.status_code == 204
    assert after["start"] == later["start"] and after["label"] is None
    assert after["id"] == created["id"]
    assert deleted.status_code == 204
    assert http.get(url).status_code == 404
    assert http.get("/schedules/1/closures").get_json() == {"total": 0, "items": []}
    assert http.schedule.exceptional_periods == []
    assert http.storage.list_schedule_ids() == []
    assert http.auth.acls[2:6] == [
        f"confd.schedules.1.closures.{created['id']}.read",
        f"confd.schedules.1.closures.{created['id']}.update",
        f"confd.schedules.1.closures.{created['id']}.read",
        f"confd.schedules.1.closures.{created['id']}.delete",
    ]


def test_closures_and_holidays_are_independent_resources(http):
    body, start, _ = seminar_body()
    http.post("/schedules/1/closures", body)
    http.put("/schedules/1/holidays", {"country": "NL"})
    http.post("/schedules/1/exceptional-periods/sync")
    assert (12, 25) in days(http.schedule)

    assert http.delete("/schedules/1/holidays").status_code == 204

    next_day = start + datetime.timedelta(days=1)
    assert days(http.schedule) == sorted(
        [(start.month, start.day), (next_day.month, next_day.day)]
    )
    assert http.get("/schedules/1/holidays").status_code == 404
    assert http.get("/schedules/1/closures").get_json()["total"] == 1


def test_put_holidays_keeps_the_closures(http):
    body, _, _ = seminar_body()
    http.post("/schedules/1/closures", body)

    http.put("/schedules/1/holidays", {"country": "NL"})

    assert http.get("/schedules/1/closures").get_json()["total"] == 1
    assert "closures" not in http.get("/schedules/1/holidays").get_json()


def test_both_sync_routes_apply_the_configuration(http):
    http.put("/schedules/1/holidays", {"country": "NL"})

    new = http.post("/schedules/1/exceptional-periods/sync")
    alias = http.post("/schedules/1/holidays/sync")

    assert new.status_code == 200 and new.get_json()["changed"] is True
    assert alias.get_json()["changed"] is False
    assert http.auth.acls[-2:] == [
        "confd.schedules.1.exceptional_periods.sync.create",
        "confd.schedules.1.holidays.sync.create",
    ]


@pytest.mark.parametrize(
    "closure",
    [
        {"start": "2027-11-20T14:00", "end": "2027-11-19T12:00"},
        {"start": "2027-11-19T12:00"},
        {"start": "2027-11-19", "end": "2027-11-20T14:00"},
        {"start": "2027-11-19T12:00:30", "end": "2027-11-20T14:00"},
        {"start": "2027-11-19T12:00Z", "end": "2027-11-20T14:00"},
        {"start": "2027-11-19T12:00", "end": "2027-11-20T14:00", "label": ""},
    ],
)
def test_post_rejects_invalid_closures(http, closure):
    response = http.post("/schedules/1/closures", closure)

    assert response.status_code == 400
    assert http.storage.list_schedule_ids() == []


def test_closure_errors_and_tenant_isolation(http):
    body, _, _ = seminar_body()

    assert http.get("/schedules/1/closures/nope").status_code == 404
    assert http.put("/schedules/1/closures/nope", body).status_code == 404
    assert http.delete("/schedules/1/closures/nope").status_code == 404
    assert http.get("/schedules/2/closures").status_code == 404
    assert http.post("/schedules/2/closures", body).status_code == 404
    assert http.post("/schedules/2/exceptional-periods/sync").status_code == 404
    assert http.get("/schedules/99/closures").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"country": "ZZ"},
        {"country": "FR", "subdivision": "67"},
        {"country": "NL", "excluded": ["Not a holiday"]},
        {"country": "NL", "excluded": "Easter Monday"},
        {"country": "N"},
    ],
)
def test_put_rejects_invalid_configuration(http, body):
    response = http.put("/schedules/1/holidays", body)

    assert response.status_code == 400
    assert http.storage.list_schedule_ids() == []


def test_other_tenant_schedule_is_not_found(http):
    assert http.get("/schedules/2/holidays").status_code == 404
    assert http.put("/schedules/2/holidays", {"country": "NL"}).status_code == 404
    assert http.post("/schedules/2/holidays/sync").status_code == 404
    assert http.delete("/schedules/2/holidays").status_code == 404
    assert http.get("/schedules/99/holidays").status_code == 404


def test_sync_creates_real_schedule_time_periods(http):
    http.put("/schedules/1/holidays", {"country": "NL", "excluded": []})

    response = http.post("/schedules/1/holidays/sync")

    assert response.status_code == 200
    body = response.get_json()
    assert body["changed"] is True and body["dry_run"] is False
    today = datetime.date.fromisoformat(body["today"])
    assert body["added"]
    for entry in body["added"]:
        date = datetime.date.fromisoformat(entry["date"])
        assert today <= date < today + datetime.timedelta(days=365)
    assert (12, 25) in days(http.schedule)
    assert len(http.schedule.exceptional_periods) == len(body["added"])
    for period in http.schedule.exceptional_periods:
        assert isinstance(period, ScheduleTime)
        assert period.mode == "closed"
        assert (period.hours, period.weekdays) == ("00:00-23:59", "1,2,3,4,5,6,7")
        assert (period.action, period.actionid, period.actionargs) == (
            "voicemail",
            "12",
            None,
        )
    assert http.schedules.edits == [(1, ["exceptional_periods"])]
    assert http.auth.acls[-1] == "confd.schedules.1.holidays.sync.create"


def test_sync_twice_changes_nothing_the_second_time(http):
    http.put("/schedules/1/holidays", {"country": "NL"})
    http.post("/schedules/1/holidays/sync")
    before = days(http.schedule)

    second = http.post("/schedules/1/holidays/sync").get_json()

    assert second["changed"] is False
    assert second["added"] == [] and second["removed"] == []
    assert days(http.schedule) == before
    assert len(http.schedules.edits) == 1


def test_dry_run_does_not_touch_the_schedule(http):
    http.put("/schedules/1/holidays", {"country": "NL"})

    body = http.post("/schedules/1/holidays/sync?dry_run=true").get_json()

    assert body["dry_run"] is True and body["changed"] is True
    assert body["added"]
    assert http.schedule.exceptional_periods == []
    assert http.schedules.edits == []
    assert http.get("/schedules/1/holidays").get_json()["managed"] == []


def test_excluded_holidays_are_not_created(http):
    http.put("/schedules/1/holidays", {"country": "NL", "excluded": ["Christmas Day"]})

    http.post("/schedules/1/holidays/sync")

    assert (12, 25) not in days(http.schedule)
    assert (12, 26) in days(http.schedule)


def test_manual_periods_survive_sync_and_delete(http):
    manual = ScheduleTime(
        hours_start="08:00",
        hours_end="12:00",
        week_days=[1],
        month_days=[5],
        months_list=[6],
        action="user",
        actionid="3",
    )
    http.schedule.exceptional_periods = [manual]
    http.put("/schedules/1/holidays", {"country": "NL"})

    http.post("/schedules/1/holidays/sync")
    assert (6, 5) in days(http.schedule)
    kept = [p for p in http.schedule.exceptional_periods if p.month_days == [5]][0]
    assert (kept.hours, kept.weekdays, kept.action, kept.actionid) == (
        "08:00-12:00",
        "1",
        "user",
        "3",
    )

    assert http.delete("/schedules/1/holidays").status_code == 204
    assert days(http.schedule) == [(6, 5)]
    assert http.get("/schedules/1/holidays").status_code == 404
    assert http.auth.acls[-2:] == [
        "confd.schedules.1.holidays.delete",
        "confd.schedules.1.holidays.read",
    ]


def test_changing_the_closed_destination_is_propagated(http):
    http.put("/schedules/1/holidays", {"country": "NL"})
    http.post("/schedules/1/holidays/sync")
    http.schedule.fallback_action = "user"
    http.schedule.fallback_actionid = "7"

    body = http.post("/schedules/1/holidays/sync").get_json()

    assert body["changed"] is True
    assert {(p.action, p.actionid) for p in http.schedule.exceptional_periods} == {
        ("user", "7")
    }


def test_conflict_while_the_schedule_is_locked(http):
    http.put("/schedules/1/holidays", {"country": "NL"})

    with http.storage.lock(1):
        assert http.post("/schedules/1/holidays/sync").status_code == 409
        assert http.put("/schedules/1/holidays", {"country": "NL"}).status_code == 409
        assert http.delete("/schedules/1/holidays").status_code == 409


def test_routes_coexist_with_the_native_schedule_plugin(tmp_path):
    from wazo_confd.plugins.schedule.plugin import Plugin as ScheduleNativePlugin

    app = Flask(__name__)
    api = Api(app)
    ScheduleNativePlugin().load({"api": api})
    plugin_module.Plugin().load(
        {"api": api, "config": {"schedule_holidays": {"directory": str(tmp_path)}}}
    )

    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert {
        "/schedules",
        "/schedules/<int:id>",
        "/schedules/<int:id>/holidays",
        "/schedules/<int:id>/holidays/sync",
    } <= rules
