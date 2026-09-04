#!/bin/sh
set -eu
d="$(cd "$(dirname "$0")" && pwd)"
"$d/check_tests.sh"
"$d/check_scan.sh"
"$d/check_ruff.sh"
"$d/check_mypy.sh"
