# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+

"""Contract tests against the real wazo-confd and xivo-dao code.

They are skipped when those packages are not importable. See AGENTS.md
("Contract tests") for how to run them.
"""

collect_ignore: list[str] = []

try:
    import wazo_confd.helpers.restful  # noqa: F401
    import xivo_dao.alchemy.schedule  # noqa: F401
except ImportError:
    collect_ignore.extend(["test_http.py", "test_cli.py"])
