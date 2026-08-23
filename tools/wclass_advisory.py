#!/usr/bin/env python3
"""Repository-owned entry point for the opt-in advisory campaign runner.

The local shim supplies the two roots explicitly and forwards all campaign
arguments to ``speculative_run.py``.  This file deliberately does not parse,
store, or replay task content; campaign locking, ordinal validation, egress
confirmation, and transient result replay remain in the repository runner.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--router-root",
        required=True,
        type=Path,
        help="repository root containing the reviewed advisory tools",
    )
    parser.add_argument(
        "--campaign-root",
        required=True,
        type=Path,
        help="owner-only output root for one advisory campaign",
    )
    return parser


def _load_runner(router_root: Path) -> ModuleType:
    tools_root = router_root.expanduser().resolve() / "tools"
    runner_path = tools_root / "speculative_run.py"
    if not runner_path.is_file():
        raise ValueError()
    if str(tools_root) not in sys.path:
        sys.path.insert(0, str(tools_root))
    spec = importlib.util.spec_from_file_location("wclass_advisory_runner", runner_path)
    if spec is None or spec.loader is None:
        raise ValueError()
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments, forwarded = parser.parse_known_args(argv)
    if any(argument == "--out-dir" or argument.startswith("--out-dir=") for argument in forwarded):
        parser.error("--out-dir is controlled by --campaign-root")
    try:
        runner = _load_runner(arguments.router_root)
    except (OSError, ValueError):
        parser.error("invalid router root")
    campaign_root = arguments.campaign_root.expanduser().resolve()
    runner_arguments = [*forwarded, "--out-dir", str(campaign_root)]
    original_argv = sys.argv
    sys.argv = [str(arguments.router_root / "tools" / "speculative_run.py"), *runner_arguments]
    try:
        return int(runner.main())
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
