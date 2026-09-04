#!/bin/sh
# 계획서 기본 verify: 테스트 + 바이트 스캔.
set -eu
d="$(cd "$(dirname "$0")" && pwd)"
"$d/check_tests.sh"
"$d/check_scan.sh"
