#!/bin/sh
# Copyright 2026 The Wazo Authors  (see the AUTHORS file)
# SPDX-License-Identifier: GPL-3.0+
#
# Rebuild vendor/ from requirements-vendor.txt. Needs network access.
# Usage: PYTHON=python3 scripts/vendor.sh

set -eu

PYTHON="${PYTHON:-python3}"
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

"$PYTHON" -m pip install --quiet --no-deps --no-compile --target "$tmp" \
    -r "$root/requirements-vendor.txt"

rm -rf "$root/vendor"
mkdir -p "$root/vendor/licenses"
cp -R "$tmp/holidays" "$tmp/dateutil" "$tmp/six.py" "$root/vendor/"
# holidays/version.py reads its version with importlib.metadata at import time,
# which needs the distribution metadata next to the package.
cp -R "$tmp"/holidays-*.dist-info "$root/vendor/"
find "$root/vendor" -name __pycache__ -type d -prune -exec rm -rf {} +

for dist in "$tmp"/*.dist-info; do
    name="$(basename "$dist" .dist-info)"
    find "$dist" -type f -iname 'LICENSE*' | while read -r licence; do
        cp "$licence" "$root/vendor/licenses/$name-$(basename "$licence")"
    done
done
