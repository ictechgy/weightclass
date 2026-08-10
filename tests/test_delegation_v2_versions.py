import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import weightclass.cli as cli
import weightclass.delegation_schema as delegation_schema
from tests.test_delegation import _manifest, _policy
from tests.test_delegation_v2_schema import valid_manifest, valid_policy
from weightclass.cli import InvalidInputError, build_parser, main
from weightclass.delegation_v2_versions import DelegationVersionError, dispatch_delegation_versions
from weightclass.json_input import load_json_object


class DelegationV2VersionTests(unittest.TestCase):
    def test_only_exact_protocol_tuples_dispatch(self) -> None:
        self.assertEqual(dispatch_delegation_versions((1, 1, 1, 1, "WCD1")), 1)
        self.assertEqual(dispatch_delegation_versions((2, 2, 2, 2, "WCD2")), 2)

    def test_mixed_wrong_type_and_unsupported_tuples_fail_without_values(self) -> None:
        for value in ((2, 2, 1, 2, "WCD2"), (True, 1, 1, 1, "WCD1"), (3, 3, 3, 3, "WCD3")):
            with self.subTest(value=value), self.assertRaises(DelegationVersionError) as caught:
                dispatch_delegation_versions(value)
            self.assertEqual(caught.exception.args, ())

    def test_delegate_route_and_run_accept_source_profile_only(self) -> None:
        for command in ("route", "run"):
            parsed = build_parser().parse_args(
                [
                    "delegate",
                    command,
                    "--policy",
                    "p",
                    "--runtime-manifest",
                    "m",
                    "--delegation-runtime",
                    "/r",
                    "--source-vendor",
                    "codex",
                    "--source-profile",
                    "source",
                    "--tier",
                    "low",
                ]
            )
            self.assertEqual(parsed.source_profile, "source")
        with self.assertRaises(InvalidInputError):
            build_parser().parse_args(
                [
                    "delegate",
                    "qualification-candidate",
                    "--evidence",
                    "e",
                    "--delegation-runtime",
                    "/r",
                    "--source-profile",
                    "source",
                ]
            )

    def test_protocol_one_rejects_source_profile_after_file_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            manifest = Path(directory) / "manifest.json"
            policy.write_text(json.dumps(_policy()), encoding="utf-8")
            manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(
                    [
                        "delegate",
                        "route",
                        "--policy",
                        str(policy),
                        "--runtime-manifest",
                        str(manifest),
                        "--delegation-runtime",
                        "/r",
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "source",
                        "--tier",
                        "low",
                    ]
                )
            self.assertEqual(result, 2)
            self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_input"})

    def test_protocol_one_without_source_profile_uses_only_legacy_loader_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            manifest = Path(directory) / "manifest.json"
            policy.write_text(json.dumps(_policy()), encoding="utf-8")
            manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
            common = [
                "--policy",
                str(policy),
                "--runtime-manifest",
                str(manifest),
                "--delegation-runtime",
                "/r",
                "--source-vendor",
                "codex",
                "--tier",
                "standard",
            ]
            for command, expected in (("route", 0), ("run", 5)):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    self.subTest(command=command),
                    mock.patch.object(cli, "load_json_object", wraps=load_json_object) as v2_loader,
                    mock.patch.object(
                        delegation_schema, "load_json_object", wraps=load_json_object
                    ) as legacy_loader,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    result = main(["delegate", command, *common])
                self.assertEqual(result, expected)
                self.assertEqual(v2_loader.call_count, 0)
                self.assertEqual(legacy_loader.call_count, 2)

    def test_protocol_two_qualification_rejection_precedes_later_run_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            manifest = Path(directory) / "manifest.json"
            policy.write_text(json.dumps(valid_policy()), encoding="utf-8")
            manifest.write_text(json.dumps(valid_manifest()), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(
                    [
                        "delegate",
                        "run",
                        "--policy",
                        str(policy),
                        "--runtime-manifest",
                        str(manifest),
                        "--delegation-runtime",
                        "/not-inspected",
                        "--source-vendor",
                        "codex",
                        "--source-profile",
                        "source",
                        "--tier",
                        "low",
                        "--require-qualified-runtime",
                    ]
                )
            self.assertEqual(result, 3)
            self.assertEqual(json.loads(stderr.getvalue()), {"error": "unsupported_route"})


if __name__ == "__main__":
    unittest.main()
