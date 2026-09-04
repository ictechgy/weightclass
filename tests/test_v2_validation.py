import json
import unittest
from pathlib import Path
from typing import Any, cast

from weightclass.v2_validation import (
    NATIVE_LIST_PATHS,
    V2ValidationError,
    canonicalize_registered_lists,
    require_exact_keys,
    require_integer,
    require_string,
)

ROOT = Path(__file__).resolve().parents[1]


class V2ValidationTests(unittest.TestCase):
    def test_scalar_primitives_exclude_bool_and_enforce_utf8_bounds(self) -> None:
        self.assertEqual(require_integer(3, lower=1, upper=3), 3)
        self.assertEqual(require_string("é", max_bytes=2), "é")
        for value in (True, 0, 4, 1.0):
            with self.subTest(value=value):
                with self.assertRaisesRegex(V2ValidationError, "^$"):
                    require_integer(value, lower=1, upper=3)
        with self.assertRaisesRegex(V2ValidationError, "^$"):
            require_string("é", max_bytes=1)

    def test_exact_keys_and_errors_are_value_free(self) -> None:
        secret = "task-secret-never-report"
        with self.assertRaises(V2ValidationError) as caught:
            require_exact_keys({"expected": 1, secret: 2}, {"expected"})
        self.assertNotIn(secret, repr(caught.exception))
        self.assertEqual(str(caught.exception), "")

    def test_every_frozen_list_path_has_one_ordering_rule(self) -> None:
        family = "native_v2_schema"
        contract = cast(
            dict[str, Any],
            json.loads((ROOT / "tests/fixtures" / family / "contract.json").read_text()),
        )
        registry = cast(dict[str, str], contract["list_paths"])
        self.assertEqual(NATIVE_LIST_PATHS, registry)
        self.assertTrue(registry)
        for path, ordering in registry.items():
            with self.subTest(path=path):
                self.assertIsInstance(ordering, str)
                self.assertTrue(ordering)
                self.assertEqual(
                    canonicalize_registered_lists({}, registry, encountered_paths=()),
                    {},
                )
                self.assertTrue(path.startswith("/"))

    def test_unknown_list_path_fails_closed(self) -> None:
        with self.assertRaisesRegex(V2ValidationError, "^$"):
            canonicalize_registered_lists(
                {"unknown": []},
                {"/known": "lexical"},
                encountered_paths=("/unknown",),
            )

    def test_registered_lists_sort_without_mutating_input(self) -> None:
        value: dict[str, Any] = {"items": [{"id": "b"}, {"id": "a"}], "argv": ["z", "a"]}
        canonical = canonicalize_registered_lists(
            value,
            {"/items": "id", "/argv": "ordered"},
            encountered_paths=("/items", "/argv"),
        )
        self.assertEqual(
            canonical,
            {"items": [{"id": "a"}, {"id": "b"}], "argv": ["z", "a"]},
        )
        self.assertEqual(value["items"][0]["id"], "b")

    def test_dimension_lists_use_declared_fixed_order(self) -> None:
        value: dict[str, Any] = {
            "transition": {
                "changed_dimensions": ["vendor", "profile"],
                "authorizations": [
                    {"dimension": "vendor", "grant_id": "v"},
                    {"dimension": "profile", "grant_id": "p"},
                ],
            }
        }
        canonical = canonicalize_registered_lists(value, NATIVE_LIST_PATHS)
        self.assertEqual(canonical["transition"]["changed_dimensions"], ["profile", "vendor"])
        self.assertEqual(
            [item["dimension"] for item in canonical["transition"]["authorizations"]],
            ["profile", "vendor"],
        )


if __name__ == "__main__":
    unittest.main()
