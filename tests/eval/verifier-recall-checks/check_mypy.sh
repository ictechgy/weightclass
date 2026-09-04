#!/bin/sh
# 저장소와 같은 mypy 핀, strict. 픽스처는 원본 상태에서 통과한다.
set -eu
exec uvx --offline mypy==2.3.0 --strict --no-incremental --cache-dir /dev/null src tests
