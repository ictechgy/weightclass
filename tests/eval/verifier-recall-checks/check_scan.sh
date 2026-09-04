#!/bin/sh
# 바이트 단위 자격증명 스캔. docs/speculative-cheap-route-design.md 의 스캔을 그대로 옮겼다.
# grep 을 쓰지 않는 이유도 그 문서에 있다: 바이너리 안의 토큰을 grep 은 놓친다.
set -eu
exec python3 - <<'SCAN'
import os, pathlib, re, sys

PATTERNS = re.compile(
    rb"sk-[A-Za-z0-9_-]{16,}"
    rb"|gh[pousr]_[A-Za-z0-9]{20,}"
    rb"|github_pat_[A-Za-z0-9_]{20,}"
    rb"|AKIA[0-9A-Z]{16}"
    rb"|xox[baprs]-[A-Za-z0-9-]{10,}"
    rb"|BEGIN [A-Z ]*PRIVATE KEY"
)
for path in pathlib.Path(".").rglob("*"):
    if ".git" in path.parts:
        continue
    haystacks = [os.fsencode(path)]
    if path.is_symlink():
        haystacks.append(os.fsencode(os.readlink(path)))
    elif path.is_file():
        haystacks.append(path.read_bytes())
    if any(PATTERNS.search(blob) for blob in haystacks):
        print(f"credential-like string at {path}", file=sys.stderr)
        sys.exit(1)
SCAN
