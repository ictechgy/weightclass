import math
import unittest
from dataclasses import FrozenInstanceError

from weightclass.canonical_v2 import bind_canonical_descriptor_v2, canonical_json_bytes_v2
from weightclass.native_v2_types import CompiledExecutionV2, FrozenCleanupV2
from weightclass.v2_validation import V2ValidationError


class CanonicalV2Tests(unittest.TestCase):
    def test_canonical_json_contract(self) -> None:
        self.assertEqual(
            canonical_json_bytes_v2({"z": "é", "a": 1}),
            b'{"a":1,"z":"\\u00e9"}',
        )
        with self.assertRaisesRegex(V2ValidationError, "^$"):
            canonical_json_bytes_v2({"number": math.nan})

    def test_fingerprint_payload_removes_only_top_level_fingerprint(self) -> None:
        descriptor = {
            "nested": {"route_fingerprint": "kept"},
            "route_fingerprint": "old",
            "x": 1,
        }
        compiled = bind_canonical_descriptor_v2(
            descriptor,
            argv=("/owned/tool", "arg"),
            executable="/owned/tool",
            transport="native_stdin",
            transport_version=1,
            cleanup=FrozenCleanupV2(1, 2),
        )
        self.assertEqual(
            compiled.fingerprint_payload_bytes,
            b'{"nested":{"route_fingerprint":"kept"},"x":1}',
        )
        self.assertTrue(compiled.route_fingerprint.startswith("sha256:"))
        self.assertIn(compiled.route_fingerprint.encode(), compiled.canonical_descriptor_bytes)

    def test_compiled_execution_retains_only_immutable_truth(self) -> None:
        source = {"items": ["safe"]}
        compiled = bind_canonical_descriptor_v2(
            source,
            argv=("/owned/tool", "arg"),
            executable="/owned/tool",
            transport="native_stdin",
            transport_version=1,
            cleanup=FrozenCleanupV2(1, 2),
        )
        source["items"].append("mutated")
        self.assertNotIn(b"mutated", compiled.canonical_descriptor_bytes)
        self.assertEqual(
            set(CompiledExecutionV2.__dataclass_fields__),
            {
                "canonical_descriptor_bytes",
                "fingerprint_payload_bytes",
                "route_fingerprint",
                "argv",
                "executable",
                "transport",
                "transport_version",
                "cleanup",
            },
        )
        with self.assertRaises(FrozenInstanceError):
            compiled.argv = ("changed",)  # type: ignore[misc]

    def test_task_content_is_not_a_fingerprint_or_compiled_input(self) -> None:
        self.assertNotIn("task", bind_canonical_descriptor_v2.__annotations__)


if __name__ == "__main__":
    unittest.main()
