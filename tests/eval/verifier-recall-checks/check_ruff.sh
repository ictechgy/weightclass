#!/bin/sh
# 저장소와 같은 ruff 핀과 규칙 집합. 오프라인이어야 하며, 없으면 설치를 시도하지 않고 실패한다.
set -eu
exec uvx --offline ruff==0.16.2 check --select E,F,W,I,UP,B --line-length 100 src tests
