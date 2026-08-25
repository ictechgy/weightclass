from __future__ import annotations

import os
import unittest

from weightclass.advisory.advisory_campaign import (
    CampaignError,
    CampaignManifest,
    record_binding,
    validate_record_bindings,
)


@unittest.skipUnless(
    os.environ.get("WCLASS_CAMPAIGN_DIAGNOSTICS_ACCEPTANCE") == "1",
    "prospective campaign diagnostics acceptance",
)
class AdvisoryCampaignDiagnosticsAcceptanceTests(unittest.TestCase):
    def manifest(self) -> CampaignManifest:
        return {
            "schema_version": 1,
            "arm": "shape_b",
            "max_tasks": 8,
            "campaign_fingerprint": "sha256:" + "a" * 64,
        }

    def test_fingerprint_mismatch_has_a_fixed_value_free_reason(self) -> None:
        manifest = self.manifest()
        mismatched = {
            "campaign": {
                **record_binding(manifest, 1),
                "campaign_fingerprint": "sha256:" + "b" * 64,
            }
        }

        with self.assertRaises(CampaignError) as captured:
            validate_record_bindings(manifest, [mismatched])

        message = str(captured.exception)
        self.assertEqual(message, "campaign_record_binding_mismatch")
        self.assertNotIn("a" * 64, message)
        self.assertNotIn("b" * 64, message)

    def test_record_shape_and_ordinal_failures_have_fixed_reasons(self) -> None:
        manifest = self.manifest()
        cases = (
            ({"campaign": {}}, "campaign_record_binding_invalid"),
            (
                {
                    "campaign": {
                        **record_binding(manifest, 1),
                        "sample_ordinal": 0,
                    }
                },
                "campaign_record_ordinal_invalid",
            ),
        )
        for record, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(CampaignError, f"^{expected}$"):
                    validate_record_bindings(manifest, [record])

        duplicate = [
            {"campaign": record_binding(manifest, 1)},
            {"campaign": record_binding(manifest, 1)},
        ]
        with self.assertRaisesRegex(CampaignError, "^campaign_record_ordinal_duplicate$"):
            validate_record_bindings(manifest, duplicate)

        gap = [
            {"campaign": record_binding(manifest, 1)},
            {"campaign": record_binding(manifest, 3)},
        ]
        with self.assertRaisesRegex(CampaignError, "^campaign_record_ordinal_gap$"):
            validate_record_bindings(manifest, gap)


if __name__ == "__main__":
    unittest.main()
