from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from weightclass import usage_aggregation as usage


@unittest.skipUnless(
    os.environ.get("WCLASS_CAMPAIGN_ACCEPTANCE") == "1",
    "prospective campaign acceptance only",
)
class UsageAggregateBoundsTests(unittest.TestCase):
    def populated_store(self, root: Path) -> tuple[Path, dict[str, object]]:
        store = root / "usage-v1.json"
        usage.ensure_usage_store(store)
        usage.set_relative_cost_weight(store, "grok", None, "low", "0.25")
        usage.set_relative_cost_weight(store, "grok", None, "medium", "1.0")
        dimensions = usage.UsageDimensions("grok", None, "low", "low")
        for _ in range(2):
            usage.record_usage(
                store,
                dimensions,
                child_returncode=0,
                rework=False,
                escalation=False,
            )
        payload = json.loads(store.read_text(encoding="ascii"))
        self.assertIsInstance(payload, dict)
        return store, payload

    def write_payload(self, store: Path, payload: dict[str, object]) -> None:
        store.write_text(json.dumps(payload), encoding="ascii")
        store.chmod(0o600)

    def assert_rejected(self, store: Path, payload: dict[str, object]) -> None:
        self.write_payload(store, payload)
        with self.assertRaisesRegex(usage.UsageAggregationError, "^$"):
            usage.render_usage_report(store)

    def test_bucket_total_must_be_feasible_for_its_weighted_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, original = self.populated_store(Path(directory))
            for total in (1, 2 * usage.MAX_WEIGHT_MICROS + 1):
                with self.subTest(total=total):
                    payload = copy.deepcopy(original)
                    buckets = payload["buckets"]
                    assert isinstance(buckets, list)
                    bucket = buckets[0]
                    assert isinstance(bucket, dict)
                    bucket["relative_cost_micros_total"] = total
                    self.assert_rejected(store, payload)

    def test_baseline_total_must_be_feasible_for_its_counted_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, original = self.populated_store(Path(directory))
            for total in (1, 2 * usage.MAX_WEIGHT_MICROS + 1):
                with self.subTest(total=total):
                    payload = copy.deepcopy(original)
                    baseline = payload["baseline"]
                    assert isinstance(baseline, dict)
                    baseline["relative_cost_micros_total"] = total
                    self.assert_rejected(store, payload)

    def test_exact_feasible_bounds_remain_valid_historical_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, original = self.populated_store(Path(directory))
            for total in (2, 2 * usage.MAX_WEIGHT_MICROS):
                with self.subTest(total=total):
                    payload = copy.deepcopy(original)
                    buckets = payload["buckets"]
                    baseline = payload["baseline"]
                    assert isinstance(buckets, list)
                    assert isinstance(buckets[0], dict)
                    assert isinstance(baseline, dict)
                    buckets[0]["relative_cost_micros_total"] = total
                    baseline["relative_cost_micros_total"] = total
                    self.write_payload(store, payload)
                    report = usage.render_usage_report(store)
                    self.assertEqual(report["schema_version"], usage.STORE_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
