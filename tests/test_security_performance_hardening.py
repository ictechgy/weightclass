from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weightclass import json_input


class SecurityPerformanceHardeningTests(unittest.TestCase):
    def test_shared_json_loader_rejects_large_integer_before_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text('{"value":' + "9" * 129 + "}", encoding="ascii")
            with self.assertRaisesRegex(json_input.JsonInputError, "^$"):
                json_input.load_json_object(path, max_bytes=1_024)


if __name__ == "__main__":
    unittest.main()
