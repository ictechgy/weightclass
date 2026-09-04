#!/bin/sh
set -eu
d="$(cd "$(dirname "$0")" && pwd)"
"$d/check_tests.sh"
