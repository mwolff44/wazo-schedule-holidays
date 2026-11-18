#!/bin/sh
# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+
#
# Run the whole test suite, including tests/contract, against the real
# wazo-confd and xivo-dao code of a given Wazo release (default: wazo-26.10).
# Needs network access and `uv`. Usage: scripts/contract-tests.sh [wazo-26.10]

set -eu

release="${1:-wazo-26.10}"
root="$(cd "$(dirname "$0")/.." && pwd)"
work="${CONTRACT_DIR:-$root/.contract}"
mkdir -p "$work"

for repo in wazo-confd xivo-dao; do
    if [ ! -d "$work/$repo" ]; then
        git clone --quiet "https://github.com/wazo-platform/$repo.git" "$work/$repo"
    fi
    git -C "$work/$repo" checkout --quiet "$release"
done
if [ ! -d "$work/venv" ]; then
    uv venv --quiet --python 3.11 "$work/venv"
    uv pip install --quiet --python "$work/venv/bin/python" -r "$root/requirements-contract.txt"
fi

cd "$root"
PYTHONPATH="$work/wazo-confd:$work/xivo-dao" "$work/venv/bin/python" -m pytest tests -q -p no:cacheprovider
