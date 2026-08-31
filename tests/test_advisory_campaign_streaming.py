from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weightclass.advisory import advisory_campaign


class AdvisoryCampaignStreamingTests(unittest.TestCase):
    def test_cr_lf_and_crlf_records_survive_chunk_boundaries(self) -> None:
        prefix = b'{"value":"'
        suffix = b'"}'
        first = prefix + b"x" * (65_535 - len(prefix) - len(suffix)) + suffix
        second = b'{"second":2}'
        third = b'{"third":3}'
        payload = first + b"\r\n" + second + b"\r" + third + b"\n"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            path.write_bytes(payload)

            self.assertEqual(
                list(advisory_campaign._iter_bound_records(path)),
                [
                    {"value": "x" * (65_535 - len(prefix) - len(suffix))},
                    {"second": 2},
                    {"third": 3},
                ],
            )

    def test_trailing_partial_is_only_ignored_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            path.write_bytes(b'{"complete":true}\n{"partial":')

            with self.assertRaises(advisory_campaign.CampaignError):
                list(advisory_campaign._iter_bound_records(path))
            self.assertEqual(
                list(advisory_campaign._iter_bound_records(path, allow_trailing_partial=True)),
                [{"complete": True}],
            )

    def test_duplicate_keys_and_oversized_integers_remain_rejected(self) -> None:
        payloads = (
            b'{"key":1,"key":2}\n',
            b'{"key":' + b"9" * (advisory_campaign.MAX_JSON_INTEGER_DIGITS + 1) + b"}\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            for payload in payloads:
                with self.subTest(payload_length=len(payload)):
                    path.write_bytes(payload)
                    with self.assertRaises(advisory_campaign.CampaignError):
                        list(advisory_campaign._iter_bound_records(path))

    def test_only_cr_and_lf_are_record_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            for separator in (b"\v", b"\f", b"\x1c", b"\x1d", b"\x1e", b"\x85"):
                with self.subTest(separator=separator):
                    path.write_bytes(b'{"first":1}' + separator + b'{"second":2}')
                    with self.assertRaises(advisory_campaign.CampaignError):
                        list(advisory_campaign._iter_bound_records(path))

    def test_byte_record_and_count_limits_remain_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            path.write_bytes(b'{"value":1}\n')
            with mock.patch.object(
                advisory_campaign, "MAX_CAMPAIGN_LOG_BYTES", len(path.read_bytes()) - 1
            ):
                with self.assertRaises(advisory_campaign.CampaignError):
                    list(advisory_campaign._iter_bound_records(path))

            with mock.patch.object(advisory_campaign, "MAX_CAMPAIGN_RECORD_BYTES", 5):
                with self.assertRaises(advisory_campaign.CampaignError):
                    list(advisory_campaign._iter_bound_records(path))

            path.write_bytes(b'{"one":1}\n{"two":2}\n')
            with mock.patch.object(advisory_campaign, "MAX_TASKS", 1):
                with self.assertRaisesRegex(
                    advisory_campaign.CampaignError, "^campaign_record_capacity_exceeded$"
                ):
                    advisory_campaign.load_bound_records(path)

    def test_incomplete_record_scanning_advances_across_chunks(self) -> None:
        payload = b'{"value":"' + b"x" * 900_000 + b'"}'
        calls: list[tuple[int, int]] = []
        original = advisory_campaign._next_line_boundary

        def instrumented(buffer: bytearray, start: int) -> int:
            calls.append((len(buffer), start))
            return original(buffer, start)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.jsonl"
            path.write_bytes(payload)
            with mock.patch.object(advisory_campaign, "_next_line_boundary", instrumented):
                records = list(advisory_campaign._iter_bound_records(path))

        self.assertEqual(records, [{"value": "x" * 900_000}])
        self.assertEqual(len(calls), math.ceil(len(payload) / 65_536))
        for (previous_length, _), (_, start) in zip(calls, calls[1:], strict=False):
            self.assertEqual(start, previous_length)


if __name__ == "__main__":
    unittest.main()
