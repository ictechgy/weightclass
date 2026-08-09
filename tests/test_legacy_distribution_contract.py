import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LegacyDistributionContractTests(unittest.TestCase):
    def test_distribution_verifier_keeps_exact_two_artifact_contract(self) -> None:
        source = (ROOT / "tests/verify_distribution_isolation.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertTrue(
            any(
                isinstance(node, ast.FunctionDef) and node.name == "_distribution_snapshot"
                for node in ast.walk(tree)
            )
        )
        self.assertIn("exactly one wheel and one sdist", source)

    def test_production_qualification_registry_remains_canonical_and_empty(self) -> None:
        raw = (ROOT / "src/weightclass/delegation_qualifications.json").read_bytes()
        self.assertEqual(
            raw,
            b'{"records":[],"registry_schema_version":1,"suite_revision":"delegation-conformance-v2"}\n',
        )


if __name__ == "__main__":
    unittest.main()
