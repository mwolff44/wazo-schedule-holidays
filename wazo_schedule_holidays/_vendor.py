# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

"""Access to the vendored ``holidays`` library.

Debian bookworm only ships ``python3-holidays`` 0.10.1, whose API differs, and
wazo-plugind does not manage pip dependencies: the library is therefore embedded
in the plugin package (see ``scripts/vendor.sh``).

The vendor directory is added to ``sys.path`` only while importing, so the
``sys.path`` of the hosting process (wazo-confd) is left untouched.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

INSTALLED_VENDOR_DIR = Path("/usr/lib/wazo-schedule-holidays/vendor")
SOURCE_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"


def candidate_dirs() -> list[Path]:
    return [INSTALLED_VENDOR_DIR, SOURCE_VENDOR_DIR]


def _is_compatible(module: ModuleType) -> bool:
    return hasattr(module, "country_holidays") and hasattr(
        module, "list_supported_countries"
    )


def holidays_module() -> ModuleType:
    module = sys.modules.get("holidays")
    if module is None:
        module = _import_from_vendor()
    if not _is_compatible(module):
        raise RuntimeError(
            "an incompatible 'holidays' module is already loaded "
            f"({getattr(module, '__file__', '?')}): "
            "the plugin needs holidays >= 0.109 (country_holidays API)"
        )
    return module


def _import_from_vendor() -> ModuleType:
    for directory in candidate_dirs():
        if (directory / "holidays").is_dir():
            sys.path.insert(0, str(directory))
            try:
                return importlib.import_module("holidays")
            finally:
                sys.path.remove(str(directory))
    return importlib.import_module("holidays")
