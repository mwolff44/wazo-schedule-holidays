#!/usr/bin/env python3
# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import yaml
from setuptools import find_packages, setup

with open("wazo/plugin.yml") as file:
    metadata = yaml.load(file, Loader=yaml.SafeLoader)

setup(
    name=metadata["name"],
    version=metadata["version"],
    description=metadata["display_name"],
    author=metadata["author"],
    url=metadata["homepage"],
    packages=find_packages(exclude=["tests", "tests.*"]),
    include_package_data=True,
    package_data={"wazo_schedule_holidays": ["api.yml"]},
    entry_points={
        "console_scripts": [
            "wazo-schedule-holidays-sync = wazo_schedule_holidays.cli:main",
        ],
        "wazo_confd.plugins": [
            "schedule_holidays = wazo_schedule_holidays.plugin:Plugin",
        ],
    },
)
