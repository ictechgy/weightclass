"""검증기 재현율 러너의 순수 부분을 인프로세스로 검사한다.

러너 자체는 수백 개의 서브프로세스를 띄우는 수동 도구라 여기서 실행하지
않는다. 카탈로그 검증, Wilson 구간, 사전 등록 §6 결정 규칙만 고정한다.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tests" / "eval" / "verifier_recall.py"


def _load_runner() -> ModuleType:
    specification = importlib.util.spec_from_file_location("verifier_recall", RUNNER)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    # 3.14 의 지연 애너테이션은 dataclass 가 sys.modules 에서 모듈을 찾게 한다.
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


CLASSES = [
    ("schema-coercion", 5),
    ("silent-overwrite", 5),
    ("id-padding", 5),
    ("mutable-leak", 5),
    ("error-swallow", 2),
    ("pagination-bound", 2),
    ("non-atomic-save", 2),
    ("credential", 4),
]
PREFIX = {
    "schema-coercion": "sc",
    "silent-overwrite": "ow",
    "id-padding": "id",
    "mutable-leak": "mu",
    "error-swallow": "es",
    "pagination-bound": "pg",
    "non-atomic-save": "at",
    "credential": "cr",
}


def _write_catalogue(directory: pathlib.Path) -> pathlib.Path:
    """사전 등록 개수를 정확히 만족하는 임시 카탈로그를 만든다."""
    (directory / "d").mkdir()
    (directory / "c").mkdir()
    defects = []
    for class_id, count in CLASSES:
        for number in range(1, count + 1):
            entry_id = f"{PREFIX[class_id]}-{number:02d}"
            (directory / "d" / f"{entry_id}.patch").write_text("", encoding="utf-8")
            defects.append({"id": entry_id, "class_id": class_id, "patch": f"d/{entry_id}.patch"})
    controls = []
    for number in range(1, 11):
        entry_id = f"ctl-{number:02d}"
        (directory / "c" / f"{entry_id}.patch").write_text("", encoding="utf-8")
        controls.append({"id": entry_id, "patch": f"c/{entry_id}.patch", "kind": "refactor"})
    catalogue = {
        "schema_version": 1,
        "classes": [{"class_id": class_id, "n": count} for class_id, count in CLASSES],
        "defects": defects,
        "controls": controls,
    }
    path = directory / "catalogue.json"
    path.write_text(json.dumps(catalogue), encoding="utf-8")
    return path


class WilsonTests(unittest.TestCase):
    def test_known_values(self) -> None:
        runner = _load_runner()
        lower, upper = runner.wilson_interval(25, 26)
        self.assertGreaterEqual(lower, 0.80)
        self.assertLessEqual(upper, 1.0)
        lower, _ = runner.wilson_interval(24, 26)
        self.assertLess(lower, 0.80)
        self.assertEqual(runner.wilson_interval(0, 0), (0.0, 1.0))


class CatalogueTests(unittest.TestCase):
    def test_a_registration_shaped_catalogue_loads(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            _, entries = runner.load_catalogue(_write_catalogue(base), base_dir=base)
        self.assertEqual(len(entries), 40)
        self.assertEqual(sum(entry.is_control for entry in entries), 10)

    def test_count_mismatch_and_duplicates_fail_closed(self) -> None:
        """Breaks if the runner accepts a catalogue that differs from the registration."""
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            path = _write_catalogue(base)
            catalogue = json.loads(path.read_text(encoding="utf-8"))
            catalogue["defects"].pop()
            path.write_text(json.dumps(catalogue), encoding="utf-8")
            with self.assertRaises(runner.CatalogueError):
                runner.load_catalogue(path, base_dir=base)
            catalogue = json.loads(_write_catalogue(pathlib.Path(tempfile.mkdtemp())).read_text())
            catalogue["controls"][1]["id"] = catalogue["controls"][0]["id"]
            path.write_text(json.dumps(catalogue), encoding="utf-8")
            with self.assertRaises(runner.CatalogueError):
                runner.load_catalogue(path, base_dir=base)

    def test_a_patch_outside_the_base_directory_is_rejected(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            path = _write_catalogue(base)
            catalogue = json.loads(path.read_text(encoding="utf-8"))
            catalogue["defects"][0]["patch"] = "../escape.patch"
            path.write_text(json.dumps(catalogue), encoding="utf-8")
            with self.assertRaises(runner.CatalogueError):
                runner.load_catalogue(path, base_dir=base)


class DecisionRuleTests(unittest.TestCase):
    def _entries_and_runs(
        self, runner: ModuleType, *, caught: int, credential_fired: bool, controls_passed: int
    ) -> tuple[list[object], dict[str, dict[str, str]]]:
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            _, entries = runner.load_catalogue(_write_catalogue(base), base_dir=base)
        runs: dict[str, dict[str, str]] = {}
        scored = 0
        controls = 0
        for entry in entries:
            if entry.is_control:
                controls += 1
                status = "passed" if controls <= controls_passed else "caught"
            elif entry.class_id == "credential":
                status = "caught" if credential_fired else "passed"
            else:
                scored += 1
                status = "caught" if scored <= caught else "passed"
            names = list(runner.CHECKS) + list(runner.COMPOSITIONS)
            runs[entry.entry_id] = dict.fromkeys(names, status)
        return list(entries), runs

    def test_go_needs_the_lower_bound_all_credentials_and_all_controls(self) -> None:
        runner = _load_runner()
        entries, runs = self._entries_and_runs(
            runner, caught=25, credential_fired=True, controls_passed=10
        )
        self.assertEqual(runner.summarize(entries, runs)["decision"]["C4"], "go")
        for caught, fired, controls in ((24, True, 10), (25, False, 10), (25, True, 9)):
            entries, runs = self._entries_and_runs(
                runner, caught=caught, credential_fired=fired, controls_passed=controls
            )
            with self.subTest(caught=caught, fired=fired, controls=controls):
                self.assertEqual(runner.summarize(entries, runs)["decision"]["C4"], "no-go")

    def test_an_unavailable_status_blocks_the_report(self) -> None:
        """Breaks if a check that could not run is counted as passed or caught."""
        runner = _load_runner()
        entries, runs = self._entries_and_runs(
            runner, caught=25, credential_fired=True, controls_passed=10
        )
        runs["sc-01"]["C4"] = "check_unavailable"
        summary = runner.summarize(entries, runs)
        self.assertEqual(summary["decision"]["C4"], "not_reported")
        self.assertFalse(summary["recall"]["C4"]["reportable"])
        self.assertEqual(summary["decision"]["C1"], "go")


if __name__ == "__main__":
    unittest.main()
