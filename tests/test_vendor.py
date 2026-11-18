# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

import sys
import types

import pytest

from wazo_schedule_holidays import _vendor


def test_vendored_holidays_has_modern_api():
    module = _vendor.holidays_module()

    assert hasattr(module, "country_holidays")
    assert module.__version__ == "0.109"


def test_vendor_dir_is_not_left_in_sys_path():
    _vendor.holidays_module()

    assert all(str(path) not in sys.path for path in _vendor.candidate_dirs())


def test_incompatible_preloaded_module_is_rejected(monkeypatch):
    monkeypatch.setitem(sys.modules, "holidays", types.ModuleType("holidays"))

    with pytest.raises(RuntimeError, match="holidays"):
        _vendor.holidays_module()
