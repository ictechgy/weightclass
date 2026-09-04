"""픽스처의 수용 테스트.

일부러 평범하게 쓴다. 태스크의 수용 기준을 확인하는 정도이지, 결함 클래스를
겨냥해 적대적으로 쓰지 않는다. 그래야 "실제 수용 테스트를 통과한 결함" 이라는
기록된 역사와 같은 강도가 된다. 캘리브레이션: 문서화된 정준 결함 네 개는 이
파일을 통과해야 한다.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ledger import (
    Ledger,
    LedgerIdError,
    SchemaError,
    StateCorruptError,
    load_state,
    normalize_ledger_id,
    page,
    save_state,
    validate_schema_version,
)


class SchemaAcceptance(unittest.TestCase):
    def test_version_one_is_accepted(self) -> None:
        self.assertEqual(validate_schema_version(1), 1)

    def test_other_versions_are_rejected(self) -> None:
        with self.assertRaises(SchemaError):
            validate_schema_version(2)


class StateAcceptance(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(path, {"schema_version": 1, "entries": ["a"]})
            self.assertEqual(load_state(path), {"schema_version": 1, "entries": ["a"]})

    def test_missing_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(StateCorruptError):
                load_state(Path(directory) / "absent.json")

    def test_saved_file_is_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(path, {"schema_version": 1})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1)


class IdAcceptance(unittest.TestCase):
    def test_plain_ids_pass_through(self) -> None:
        self.assertEqual(normalize_ledger_id("order-2026.09"), "order-2026.09")

    def test_slash_is_rejected(self) -> None:
        with self.assertRaises(LedgerIdError):
            normalize_ledger_id("a/b")

    def test_empty_is_rejected(self) -> None:
        with self.assertRaises(LedgerIdError):
            normalize_ledger_id("")


class LedgerAcceptance(unittest.TestCase):
    def test_add_then_list(self) -> None:
        ledger = Ledger()
        ledger.add("a")
        ledger.add("b")
        self.assertEqual(list(ledger.entries()), ["a", "b"])
        self.assertEqual(len(ledger), 2)

    def test_remove(self) -> None:
        ledger = Ledger()
        ledger.add("a")
        ledger.remove("a")
        self.assertEqual(len(ledger), 0)

    def test_duplicate_is_rejected(self) -> None:
        ledger = Ledger()
        ledger.add("a")
        with self.assertRaises(KeyError):
            ledger.add("a")


class PaginationAcceptance(unittest.TestCase):
    def test_first_page(self) -> None:
        self.assertEqual(page(["a", "b", "c", "d"], 2, 0), ("a", "b"))

    def test_second_page(self) -> None:
        self.assertEqual(page(["a", "b", "c", "d"], 2, 1), ("c", "d"))

    def test_invalid_size(self) -> None:
        with self.assertRaises(ValueError):
            page(["a"], 0, 0)


if __name__ == "__main__":
    unittest.main()
