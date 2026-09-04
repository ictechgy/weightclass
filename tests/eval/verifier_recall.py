"""Measure verifier recall against an injected-defect catalogue, offline.

`tests/eval/verifier-recall-preregistration.md` fixes what this tool measures and
how it decides. This file only executes that pre-registration: it applies each
catalogued patch to a fresh copy of the frozen fixture, runs every check and every
composition in the scrubbed environment the production runner uses, and writes a
report whose numbers are bound to the fixture, catalogue, and composition
fingerprints.

It never invokes a vendor CLI or the network. It is a manual `tests/eval` tool, not
a unittest module: one run is a few hundred subprocesses.

    PYTHONPATH=src python3 tests/eval/verifier_recall.py \\
        --catalogue tests/eval/verifier_recall_catalogue.json \\
        --report /outside/the/repository/verifier-recall-report.json

Exit codes: 0 report written (go or no-go alike); 2 invalid catalogue or fixture
mismatch; 3 a check was unavailable, so recall was not reported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

EVAL_DIR = pathlib.Path(__file__).resolve().parent
FIXTURE_DIR = EVAL_DIR / "fixtures" / "verifier-recall"
CHECKS_DIR = EVAL_DIR / "verifier-recall-checks"
IDENTIFIER = re.compile(r"[a-z]{2,3}-[0-9]{2}\Z")
CHECKS: tuple[str, ...] = ("tests", "scan", "ruff", "mypy", "invariant")
COMPOSITIONS: Mapping[str, tuple[str, ...]] = {
    "C0": ("tests",),
    "C1": ("tests", "scan"),
    "C2": ("tests", "scan", "ruff"),
    "C3": ("tests", "scan", "ruff", "mypy"),
    "C4": ("tests", "scan", "ruff", "mypy", "invariant"),
}
CHECK_SCRIPTS: Mapping[str, str] = {
    "tests": "check_tests.sh",
    "scan": "check_scan.sh",
    "ruff": "check_ruff.sh",
    "mypy": "check_mypy.sh",
    "invariant": "check_invariant.py",
}
CREDENTIAL_CLASS = "credential"
STATUS_VOCABULARY = ("caught", "passed", "check_unavailable", "timeout", "apply_failed")
CHECK_TIMEOUT_SECONDS = 300
# 사전 등록 §6: Wilson 하한 0.80, 자격증명 전부 발화, 컨트롤 전부 통과.
RECALL_LOWER_BOUND_MIN = 0.80
Z_95 = 1.959964


class CatalogueError(ValueError):
    """카탈로그가 사전 등록과 맞지 않는다. 메시지는 값을 되비추지 않는다."""


@dataclass(frozen=True)
class Entry:
    """결함 또는 컨트롤 하나."""

    entry_id: str
    class_id: str
    patch: pathlib.Path
    is_control: bool


def sha256_of_tree(root: pathlib.Path) -> str:
    """정렬된 상대 경로와 내용으로 디렉터리 지문을 만든다. `__pycache__` 는 제외."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        # 도구 상태(.omc 등)와 바이트코드는 픽스처가 아니다.
        relative_parts = path.relative_to(root).parts
        if any(part.startswith(".") or part == "__pycache__" for part in relative_parts):
            continue
        if not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def sha256_of_files(paths: Iterable[pathlib.Path]) -> str:
    """구성 스크립트와 그것이 부르는 체크 스크립트를 함께 지문 낸다."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def composition_fingerprint(name: str) -> str:
    """구성 이름에 대한 지문: 구성 스크립트 + 포함된 체크 스크립트."""
    files = [CHECKS_DIR / f"verify-{name.lower()}.sh"]
    files.extend(CHECKS_DIR / CHECK_SCRIPTS[check] for check in COMPOSITIONS[name])
    return sha256_of_files(files)


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    """Wilson 95% 구간. total 이 0 이면 (0, 1)."""
    if total <= 0:
        return (0.0, 1.0)
    proportion = successes / total
    denominator = 1 + Z_95**2 / total
    centre = (proportion + Z_95**2 / (2 * total)) / denominator
    margin = Z_95 * math.sqrt(proportion * (1 - proportion) / total + Z_95**2 / (4 * total**2))
    margin /= denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def load_catalogue(
    path: pathlib.Path, base_dir: pathlib.Path = EVAL_DIR
) -> tuple[dict[str, Any], list[Entry]]:
    """카탈로그를 읽고 사전 등록 형태를 검증한다. 값은 진단에 싣지 않는다.

    `base_dir` 는 패치 경로의 기준이다. 테스트가 임시 카탈로그를 쓸 수 있게
    인자로 두되, 패치는 그 아래에만 있어야 한다.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CatalogueError("catalogue is not readable JSON") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise CatalogueError("catalogue schema_version must be 1")
    classes = raw.get("classes")
    defects = raw.get("defects")
    controls = raw.get("controls")
    if not (isinstance(classes, list) and isinstance(defects, list) and isinstance(controls, list)):
        raise CatalogueError("catalogue needs classes, defects, and controls lists")
    expected_counts = {}
    for entry in classes:
        if not isinstance(entry, dict) or not isinstance(entry.get("class_id"), str):
            raise CatalogueError("class entry is malformed")
        expected_counts[entry["class_id"]] = entry.get("n")
    entries: list[Entry] = []
    seen: set[str] = set()
    counts: dict[str, int] = {}
    for is_control, group in ((False, defects), (True, controls)):
        for entry in group:
            if not isinstance(entry, dict):
                raise CatalogueError("entry is malformed")
            entry_id = entry.get("id")
            patch = entry.get("patch")
            class_id = "control" if is_control else entry.get("class_id")
            if not (isinstance(entry_id, str) and IDENTIFIER.fullmatch(entry_id)):
                raise CatalogueError("entry id does not match the pre-registered pattern")
            if entry_id in seen:
                raise CatalogueError("duplicate entry id")
            if not isinstance(patch, str) or not isinstance(class_id, str):
                raise CatalogueError("entry patch or class is malformed")
            patch_path = (base_dir / patch).resolve()
            if base_dir.resolve() not in patch_path.parents or not patch_path.is_file():
                raise CatalogueError("entry patch is missing or outside tests/eval")
            if not is_control and class_id not in expected_counts:
                raise CatalogueError("defect names an unregistered class")
            seen.add(entry_id)
            counts[class_id] = counts.get(class_id, 0) + 1
            entries.append(Entry(entry_id, class_id, patch_path, is_control))
    for class_id, expected in expected_counts.items():
        if counts.get(class_id, 0) != expected:
            raise CatalogueError("defect count per class does not match the registration")
    if counts.get("control", 0) != 10:
        raise CatalogueError("exactly ten controls are pre-registered")
    return raw, entries


def scrubbed_environment(home: pathlib.Path) -> dict[str, str]:
    """생산 러너의 `run_verify` 와 같은 최소 환경. 하네스 허용 항목은 UV_CACHE_DIR 하나."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if name in ("PATH", "LANG", "LC_ALL", "TZ", "SHELL", "USER")
    }
    environment["HOME"] = str(home)
    environment["TMPDIR"] = str(home)
    environment["UV_OFFLINE"] = "1"
    cache = os.environ.get("UV_CACHE_DIR")
    if cache is None:
        try:
            probe = subprocess.run(
                ["uv", "cache", "dir"], capture_output=True, text=True, check=False, timeout=30
            )
            cache = probe.stdout.strip() if probe.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            cache = None
    if cache:
        environment["UV_CACHE_DIR"] = cache
    return environment


def fresh_workspace(parent: pathlib.Path, entry: Entry | None) -> pathlib.Path | None:
    """픽스처 사본을 만들고 패치를 적용한다. 적용에 실패하면 None."""
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="recall-", dir=parent)) / "fx"
    shutil.copytree(
        FIXTURE_DIR,
        workspace,
        symlinks=True,
        ignore=shutil.ignore_patterns(".*", "__pycache__"),
    )
    if entry is None:
        return workspace
    applied = subprocess.run(
        ["git", "apply", "-p1", "--unsafe-paths", str(entry.patch)],
        cwd=workspace,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if applied.returncode != 0:
        shutil.rmtree(workspace.parent, ignore_errors=True)
        return None
    return workspace


def run_script(script: pathlib.Path, workspace: pathlib.Path, home: pathlib.Path) -> str:
    """스크립트를 돌리고 상태 어휘 하나를 돌려준다. 종료 코드만 본다."""
    command = [sys.executable, str(script)] if script.suffix == ".py" else [str(script)]
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=scrubbed_environment(home),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=CHECK_TIMEOUT_SECONDS,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    except OSError:
        return "check_unavailable"
    return "passed" if completed.returncode == 0 else "caught"


def preflight(parent: pathlib.Path) -> dict[str, bool]:
    """원본 픽스처에서 각 체크가 시작해 통과하는지. 아니면 그 체크는 unavailable."""
    available: dict[str, bool] = {}
    for check, script in CHECK_SCRIPTS.items():
        workspace = fresh_workspace(parent, None)
        assert workspace is not None
        home = pathlib.Path(tempfile.mkdtemp(prefix="home-", dir=parent))
        available[check] = run_script(CHECKS_DIR / script, workspace, home) == "passed"
        shutil.rmtree(workspace.parent, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
    return available


def score_entry(
    entry: Entry, parent: pathlib.Path, available: Mapping[str, bool]
) -> dict[str, str]:
    """항목 하나에 대해 체크 다섯 개와 구성 다섯 개의 상태를 낸다."""
    results: dict[str, str] = {}
    targets: list[tuple[str, pathlib.Path, tuple[str, ...]]] = [
        (check, CHECKS_DIR / CHECK_SCRIPTS[check], (check,)) for check in CHECKS
    ]
    targets.extend(
        (name, CHECKS_DIR / f"verify-{name.lower()}.sh", parts)
        for name, parts in COMPOSITIONS.items()
    )
    for name, script, parts in targets:
        if not all(available[part] for part in parts):
            results[name] = "check_unavailable"
            continue
        workspace = fresh_workspace(parent, entry)
        if workspace is None:
            results[name] = "apply_failed"
            continue
        home = pathlib.Path(tempfile.mkdtemp(prefix="home-", dir=parent))
        results[name] = run_script(script, workspace, home)
        shutil.rmtree(workspace.parent, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
    return results


def summarize(entries: Sequence[Entry], runs: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    """사전 등록 §6 을 그대로 적용한다.

    보고 불가 상태가 하나라도 있으면 그 구성은 보고하지 않는다.
    """
    defects = [entry for entry in entries if not entry.is_control]
    controls = [entry for entry in entries if entry.is_control]
    scored = [entry for entry in defects if entry.class_id != CREDENTIAL_CLASS]
    credentials = [entry for entry in defects if entry.class_id == CREDENTIAL_CLASS]
    recall: dict[str, Any] = {}
    specificity: dict[str, Any] = {}
    decision: dict[str, Any] = {}
    for name in COMPOSITIONS:
        statuses = [runs[entry.entry_id][name] for entry in scored]
        credential_statuses = [runs[entry.entry_id][name] for entry in credentials]
        control_statuses = [runs[entry.entry_id][name] for entry in controls]
        reportable = all(
            status in ("caught", "passed")
            for status in statuses + credential_statuses + control_statuses
        )
        if not reportable:
            recall[name] = {"reportable": False}
            specificity[name] = {"reportable": False}
            decision[name] = "not_reported"
            continue
        caught = statuses.count("caught")
        lower, upper = wilson_interval(caught, len(statuses))
        by_class: dict[str, dict[str, int]] = {}
        for entry in scored:
            bucket = by_class.setdefault(entry.class_id, {"caught": 0, "n": 0})
            bucket["n"] += 1
            bucket["caught"] += runs[entry.entry_id][name] == "caught"
        credential_all_fired = all(status == "caught" for status in credential_statuses)
        controls_passed = control_statuses.count("passed")
        recall[name] = {
            "reportable": True,
            "caught": caught,
            "n": len(statuses),
            "rate": round(caught / len(statuses), 4) if statuses else None,
            "wilson95": [round(lower, 4), round(upper, 4)],
            "by_class": by_class,
            "credential_all_fired": credential_all_fired,
            "credential_n": len(credential_statuses),
        }
        specificity[name] = {
            "reportable": True,
            "passed": controls_passed,
            "total": len(control_statuses),
        }
        verdict = (
            lower >= RECALL_LOWER_BOUND_MIN
            and credential_all_fired
            and controls_passed == len(control_statuses)
        )
        decision[name] = "go" if verdict else "no-go"
    return {"recall": recall, "specificity": specificity, "decision": decision}


def build_report(
    catalogue: Mapping[str, Any],
    catalogue_path: pathlib.Path,
    entries: Sequence[Entry],
    available: Mapping[str, bool],
    runs: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """패치 본문은 싣지 않는다. 지문, 상태, 집계만."""
    summary = summarize(entries, runs)
    return {
        "schema_version": 1,
        "label": "verifier recall on the public verifier-recall fixture; not general recall",
        "bindings": {
            "fixture_fingerprint": sha256_of_tree(FIXTURE_DIR),
            "catalogue_fingerprint": "sha256:"
            + hashlib.sha256(catalogue_path.read_bytes()).hexdigest(),
            "preregistration_fingerprint": "sha256:"
            + hashlib.sha256(
                (EVAL_DIR / "verifier-recall-preregistration.md").read_bytes()
            ).hexdigest(),
            "composition_fingerprints": {
                name: composition_fingerprint(name) for name in COMPOSITIONS
            },
            "tools": {"python": sys.version.split()[0], "ruff": "0.16.2", "mypy": "2.3.0"},
        },
        "checks_available": dict(available),
        "status_vocabulary": list(STATUS_VOCABULARY),
        "runs": [
            {
                "id": entry.entry_id,
                "class_id": entry.class_id,
                "results": dict(runs[entry.entry_id]),
            }
            for entry in entries
        ],
        "provenance": catalogue.get("provenance"),
        "decision_rule": (
            "Wilson 95% lower bound of recall over non-credential defects >= 0.80, "
            "credential defects all caught, controls all passed; per composition"
        ),
        **summary,
    }


def print_summary(report: Mapping[str, Any]) -> None:
    """사람이 읽는 요약. 값은 JSON 과 같다."""
    print(report["label"])
    for name in COMPOSITIONS:
        recall = report["recall"][name]
        if not recall["reportable"]:
            print(f"  {name}: not reported (unavailable check, timeout, or apply failure)")
            continue
        specificity = report["specificity"][name]
        print(
            f"  {name}: recall {recall['caught']}/{recall['n']} "
            f"wilson95 {recall['wilson95']}, credential all fired "
            f"{recall['credential_all_fired']}, controls "
            f"{specificity['passed']}/{specificity['total']} -> {report['decision'][name]}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalogue", required=True, type=pathlib.Path)
    parser.add_argument("--report", required=True, type=pathlib.Path)
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="score only these entry ids (smoke runs; the report is then not a decision)",
    )
    arguments = parser.parse_args(argv)
    try:
        catalogue, entries = load_catalogue(arguments.catalogue.resolve())
    except CatalogueError as error:
        print(json.dumps({"error": "invalid_input", "reason": str(error)}), file=sys.stderr)
        return 2
    if catalogue.get("fixture_fingerprint") != sha256_of_tree(FIXTURE_DIR):
        print(json.dumps({"error": "invalid_input", "reason": "fixture fingerprint mismatch"}))
        return 2
    if arguments.only:
        entries = [entry for entry in entries if entry.entry_id in set(arguments.only)]
    with tempfile.TemporaryDirectory(prefix="verifier-recall-") as scratch:
        parent = pathlib.Path(scratch)
        available = preflight(parent)
        runs: dict[str, dict[str, str]] = {}
        for entry in entries:
            runs[entry.entry_id] = score_entry(entry, parent, available)
            print(f"{entry.entry_id}: {runs[entry.entry_id]}", file=sys.stderr)
    report = build_report(catalogue, arguments.catalogue.resolve(), entries, available, runs)
    if arguments.only:
        report["decision"] = {name: "not_a_decision_partial_run" for name in COMPOSITIONS}
    arguments.report.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print_summary(report)
    return 0 if all(available.values()) else 3


if __name__ == "__main__":
    raise SystemExit(main())
