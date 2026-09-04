#!/bin/sh
# C0: 픽스처의 수용 테스트만. 종료 코드가 판정이다.
set -eu
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src exec python3 -W error::ResourceWarning \
  -m unittest discover -s tests -p "ledger_acceptance.py"
