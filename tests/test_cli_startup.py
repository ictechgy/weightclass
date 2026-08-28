from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest import mock

from weightclass import classification_cli


class CliStartupTests(unittest.TestCase):
    def test_advisory_help_does_not_import_command_implementations(self) -> None:
        """The top-level help path must stay free of provider/campaign startup work."""
        program = """
import json
import sys

from weightclass.advisory.wclass_advisory import main

try:
    exit_code = main(["--help"])
except SystemExit as error:
    exit_code = error.code
forbidden = sorted(
    name
    for name in sys.modules
    if name in {
        "weightclass.advisory.advisory_campaign",
        "weightclass.advisory.advisory_experiments",
        "weightclass.advisory.advisory_orchestration",
        "weightclass.advisory.advisory_portfolio",
        "weightclass.advisory.advisory_routes",
        "weightclass.advisory.managed_advisory",
        "weightclass.advisory.speculative_report",
        "weightclass.advisory.speculative_run",
    }
)
print(json.dumps({"exit_code": exit_code, "forbidden": forbidden}))
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.splitlines()[-1]),
            {"exit_code": 0, "forbidden": []},
        )

    def test_advisory_run_help_parses_before_loading_the_runner(self) -> None:
        program = """
import json
import sys
from weightclass.advisory.wclass_advisory import main
try:
    main(["run", "--help"])
except SystemExit as error:
    exit_code = error.code
forbidden = sorted(name for name in sys.modules if name in {
    "weightclass.advisory.advisory_campaign",
    "weightclass.advisory.advisory_orchestration",
    "weightclass.advisory.speculative_run",
})
print(json.dumps({"exit_code": exit_code, "forbidden": forbidden}))
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.splitlines()[-1]),
            {"exit_code": 0, "forbidden": []},
        )

    def test_full_cli_import_defers_execution_command_families(self) -> None:
        program = """
import json
import sys

import weightclass.cli

loaded = sorted(
    name
    for name in sys.modules
    if name in {
        "weightclass.agent_discovery",
        "weightclass.delegation_compile",
        "weightclass.delegation_protocol",
        "weightclass.delegation_qualification",
        "weightclass.delegation_runtime",
        "weightclass.delegation_schema",
        "weightclass.delegation_v2_compile",
        "weightclass.delegation_v2_protocol",
        "weightclass.delegation_v2_runtime",
        "weightclass.delegation_v2_schema",
        "weightclass.executable_observation",
        "weightclass.native_v2_compile",
        "weightclass.native_v2_runtime",
        "weightclass.native_v2_schema",
        "weightclass.native_v3_compile",
        "weightclass.native_v3_runtime",
        "weightclass.native_v3_schema",
        "weightclass.native_v3_selector",
        "weightclass.task_v2",
        "weightclass.triage",
        "weightclass.v2",
        "weightclass.v2_validation",
    }
)
print(json.dumps(loaded))
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_parser_metadata_matches_its_deferred_authorities(self) -> None:
        from weightclass.adapter_registry import BUILT_IN_AGENT_IDS
        from weightclass.cli import AGENT_IDS, API_SOURCE_VENDORS
        from weightclass.v2 import API_SOURCE_VENDORS as V2_API_SOURCE_VENDORS

        self.assertEqual(AGENT_IDS, BUILT_IN_AGENT_IDS)
        self.assertEqual(frozenset(API_SOURCE_VENDORS), V2_API_SOURCE_VENDORS)

    def test_schema_one_route_does_not_load_native_execution_families(self) -> None:
        program = """
import json
import sys
from unittest import mock
from weightclass.cli import main
with mock.patch("weightclass.cli.resolve_builtin_executable", return_value="/bin/echo"):
    exit_code = main(["route"])
forbidden = sorted(name for name in sys.modules if name in {
    "weightclass.native_v2_compile",
    "weightclass.native_v2_runtime",
    "weightclass.native_v3_compile",
    "weightclass.native_v3_runtime",
    "weightclass.native_v3_selector",
})
print(json.dumps({"exit_code": exit_code, "forbidden": forbidden}))
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            check=False,
            input="Fix a spelling typo.",
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.splitlines()[-1]),
            {"exit_code": 0, "forbidden": []},
        )

    def test_local_classify_does_not_mislabel_unexpected_failures_as_vendor_unavailable(
        self,
    ) -> None:
        """Breaks if the no-vendor path catches every internal exception."""
        with mock.patch.object(
            classification_cli,
            "classify_task",
            side_effect=RuntimeError("unexpected"),
        ):
            with self.assertRaises(RuntimeError):
                classification_cli.classify_task_input(read_task=lambda: "Fix a spelling typo.")

    def test_local_classify_does_not_load_unrelated_command_families(self) -> None:
        """Breaks if the fast local path eagerly imports runtime protocols again."""
        program = """
import json
import sys

from weightclass.entrypoint import main

exit_code = main(["classify"])
forbidden = sorted(
    name
    for name in sys.modules
    if name in {
        "weightclass.cli",
        "weightclass.delegation_runtime",
        "weightclass.native_v2_compile",
        "weightclass.triage",
        "weightclass.v2",
    }
)
print(json.dumps({"exit_code": exit_code, "forbidden": forbidden}))
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            check=False,
            input="Fix a spelling typo.",
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], '{"tier": "low"}')
        self.assertEqual(json.loads(lines[1]), {"exit_code": 0, "forbidden": []})
        self.assertEqual(result.stderr, "")

    def test_version_does_not_load_the_full_command_dispatcher(self) -> None:
        """Breaks if a metadata query pays for every runtime protocol import."""
        program = """
import json
import sys

from weightclass.entrypoint import main

exit_code = main(["--version"])
forbidden = sorted(
    name
    for name in sys.modules
    if name in {
        "weightclass.cli",
        "weightclass.delegation_runtime",
        "weightclass.native_v2_compile",
        "weightclass.triage",
        "weightclass.v2",
    }
)
print(json.dumps({"exit_code": exit_code, "forbidden": forbidden}))
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertRegex(lines[0], r"^weightclass [0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?$")
        self.assertEqual(json.loads(lines[1]), {"exit_code": 0, "forbidden": []})
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
