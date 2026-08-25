from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weightclass.advisory import (
    advisory_campaign,
    advisory_orchestration,
    managed_advisory,
)
from weightclass.advisory.advisory_campaign import CampaignError, CampaignManifest


def codex_profile() -> dict[str, object]:
    return {
        "schema_version": 1,
        "vendor": "codex",
        "models": {"cheap": "cheap", "advisor": "advisor", "expensive": "expensive"},
        "efforts": {"cheap": "low", "advisor": "high", "expensive": "high"},
    }


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
                **advisory_campaign.record_binding(manifest, 1),
                "campaign_fingerprint": "sha256:" + "b" * 64,
            }
        }

        with self.assertRaises(CampaignError) as captured:
            advisory_campaign.validate_record_bindings(manifest, [mismatched])

        message = str(captured.exception)
        self.assertEqual(message, "campaign_record_binding_mismatch")
        self.assertNotIn("a" * 64, message)
        self.assertNotIn("b" * 64, message)

    def test_record_shape_and_ordinal_failures_have_fixed_reasons(self) -> None:
        manifest = self.manifest()
        cases: tuple[tuple[dict[str, object], str], ...] = (
            ({"campaign": {}}, "campaign_record_binding_invalid"),
            (
                {
                    "campaign": {
                        **advisory_campaign.record_binding(manifest, 1),
                        "sample_ordinal": 0,
                    }
                },
                "campaign_record_ordinal_invalid",
            ),
        )
        for record, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(CampaignError, f"^{expected}$"):
                    advisory_campaign.validate_record_bindings(manifest, [record])

        duplicate = [
            {"campaign": advisory_campaign.record_binding(manifest, 1)},
            {"campaign": advisory_campaign.record_binding(manifest, 1)},
        ]
        with self.assertRaisesRegex(CampaignError, "^campaign_record_ordinal_duplicate$"):
            advisory_campaign.validate_record_bindings(manifest, duplicate)

        gap = [
            {"campaign": advisory_campaign.record_binding(manifest, 1)},
            {"campaign": advisory_campaign.record_binding(manifest, 3)},
        ]
        with self.assertRaisesRegex(CampaignError, "^campaign_record_ordinal_gap$"):
            advisory_campaign.validate_record_bindings(manifest, gap)

    def test_allocator_preserves_the_fixed_record_rejection_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            managed_advisory.initialize_campaign_set(
                state_root,
                profile=codex_profile(),
                prices=None,
                planned_tasks=60,
                max_tasks=150,
                dry_run=False,
            )
            selected = managed_advisory.campaign_paths(state_root, "codex", "implementation")
            request = advisory_orchestration.LaneRequest(
                "codex",
                selected.results,
                campaign_path=selected.campaign,
            )
            error = CampaignError("campaign_record_binding_mismatch")

            with (
                mock.patch.object(
                    advisory_orchestration,
                    "load_merged_lane_records",
                    side_effect=error,
                ),
                self.assertRaisesRegex(ValueError, "^campaign_record_binding_mismatch$"),
            ):
                with advisory_orchestration.acquire_campaign_lanes((request,)):
                    self.fail("an invalid campaign acquired a lane")

    def test_doctor_rejects_invalid_records_with_the_fixed_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory) / "advisory-v1"
            managed_advisory.initialize_campaign_set(
                state_root,
                profile=codex_profile(),
                prices=None,
                planned_tasks=60,
                max_tasks=150,
                dry_run=False,
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            error = CampaignError("campaign_record_binding_mismatch")

            with (
                mock.patch.object(
                    advisory_campaign,
                    "load_merged_lane_records",
                    side_effect=error,
                ),
                mock.patch("sys.stdout", stdout),
                mock.patch("sys.stderr", stderr),
            ):
                code = managed_advisory.doctor_main(
                    [
                        "--state-root",
                        str(state_root),
                        "--vendor",
                        "codex",
                        "--workflow",
                        "implementation",
                    ]
                )

            self.assertEqual(code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                json.loads(stderr.getvalue()),
                {"error": "campaign_record_binding_mismatch"},
            )
            self.assertNotIn(str(state_root), stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
