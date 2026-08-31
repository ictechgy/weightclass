from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path


class ManagedCliBoundaryTests(unittest.TestCase):
    def test_managed_help_parses_without_loading_service_backend(self) -> None:
        program = """
import json
import sys

from weightclass.advisory.wclass_advisory import main

try:
    main(["status", "--help"])
except SystemExit as error:
    exit_code = error.code
else:
    exit_code = 0
forbidden = sorted(
    name
    for name in sys.modules
    if name in {
        "weightclass.advisory.advisory_campaign",
        "weightclass.advisory.advisory_experiments",
        "weightclass.advisory.advisory_orchestration",
        "weightclass.advisory.advisory_parallel",
        "weightclass.advisory.advisory_portfolio",
        "weightclass.advisory.advisory_preflight",
        "weightclass.advisory.advisory_routes",
        "weightclass.advisory.managed_advisory",
        "weightclass.advisory.managed_verify",
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

    def test_task_free_status_does_not_load_dispatch_only_services(self) -> None:
        program = """
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weightclass.advisory import managed_advisory
from weightclass.advisory.wclass_advisory import main

selected = managed_advisory.CampaignPaths(
    Path("/state/profile"),
    Path("/state/prices"),
    Path("/state/campaign"),
    Path("/state/results"),
)
portfolio = SimpleNamespace(main=lambda arguments: 0)
with (
    mock.patch.object(managed_advisory, "_root", return_value=Path("/state")),
    mock.patch.object(managed_advisory, "_selected_vendors", return_value=("codex",)),
    mock.patch.object(managed_advisory, "_active_campaign_paths", return_value=selected),
    mock.patch.object(managed_advisory, "advisory_portfolio", portfolio),
):
    exit_code = main(["status", "--vendor", "codex", "--workflow", "review"])
forbidden = sorted(
    name
    for name in sys.modules
    if name in {
        "weightclass.advisory.advisory_experiments",
        "weightclass.advisory.advisory_orchestration",
        "weightclass.advisory.advisory_parallel",
        "weightclass.advisory.advisory_preflight",
        "weightclass.advisory.managed_verify",
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

    def test_legacy_main_wrapper_observes_patched_service(self) -> None:
        from unittest import mock

        from weightclass.advisory import managed_advisory

        with (
            mock.patch.object(managed_advisory, "_root", return_value=Path("/state")),
            mock.patch.object(managed_advisory, "_profile_from_path", return_value={}),
            mock.patch.object(
                managed_advisory,
                "initialize_campaign_set",
                return_value={"patched": True},
            ) as initialize,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = managed_advisory.init_main(["--profile", "/profile"])

        self.assertEqual(code, 0)
        initialize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
