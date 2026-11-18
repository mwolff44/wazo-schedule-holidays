# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

"""Command line entry point of the daily synchronisation (run by cron as root).

Modelled on ``wazo-confd-purge-meetings``: it does not call the REST API, it
reuses confd's schedule service directly (database, validator, bus events).
No service key is needed since wazo-auth is never contacted.
"""

import argparse
import logging
import os
import sys

from wazo_confd._bus import BusPublisher
from wazo_confd.config import DEFAULT_CONFIG
from wazo_confd.helpers.resource import CRUDService
from wazo_confd.plugins.schedule.notifier import ScheduleNotifier
from wazo_confd.plugins.schedule.validator import build_validator
from xivo import xivo_logging
from xivo.chain_map import ChainMap
from xivo.config_helper import read_config_file_hierarchy
from xivo_dao import init_db_from_config
from xivo_dao.helpers.db_utils import session_scope
from xivo_dao.helpers.exception import NotFoundError
from xivo_dao.resources.infos import dao as info_dao
from xivo_dao.resources.schedule import dao as schedule_dao

from .runner import resolve_owner, sync_all
from .wiring import build_holidays_service

logger = logging.getLogger("wazo-schedule-holidays-sync")


def parse_cli_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Synchronise the exceptional periods of the schedules "
        "with their public holidays configuration."
    )
    parser.add_argument("-d", "--debug", action="store_true", help="Log debug messages")
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Only print warnings and errors"
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Compute and log the changes without applying them",
    )
    parser.add_argument(
        "-s",
        "--schedule-id",
        type=int,
        help="Only synchronise this schedule (default: all the configured ones)",
    )
    return parser.parse_args(argv)


def log_level(args):
    if args.quiet:
        return logging.WARNING
    if args.debug:
        return logging.DEBUG
    return logging.INFO


def load_config():
    # read_config_file_hierarchy only returns what the files contain: the defaults
    # (user, bus, rest_api...) must be chained back, as wazo-confd-purge-meetings does.
    file_config = read_config_file_hierarchy(ChainMap(DEFAULT_CONFIG))
    return ChainMap(file_config, DEFAULT_CONFIG)


def main(argv=None):
    args = parse_cli_args(argv)
    config = load_config()
    xivo_logging.setup_logging("/dev/null", log_level=log_level(args))
    xivo_logging.silence_loggers(["stevedore.extension"], logging.WARNING)

    init_db_from_config(config)
    bus = BusPublisher.from_config(info_dao.get().uuid, config["bus"])
    schedule_service = CRUDService(
        schedule_dao, build_validator(), ScheduleNotifier(bus)
    )
    service, storage = build_holidays_service(
        config,
        schedule_service,
        owner=resolve_owner(config["user"], os.geteuid()),
    )

    report = sync_all(
        service,
        storage,
        scope=session_scope,
        flush=bus.flush,
        rollback=bus.rollback,
        not_found=(NotFoundError,),
        dry_run=args.dry_run,
        schedule_id=args.schedule_id,
    )
    logger.log(logging.INFO if report.ok else logging.WARNING, report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
