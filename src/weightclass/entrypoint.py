"""Small command-family dispatcher for cold CLI startup."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--version"]:
        from . import __version__

        print(f"weightclass {__version__}")
        return 0
    if (
        arguments[:1] == ["classify"]
        and not any(argument in {"--json", "--human"} for argument in arguments[1:])
        and not bool(getattr(sys.stdout, "isatty", lambda: False)())
    ):
        from .classification_cli import main as classify_main

        return classify_main(arguments[1:])

    from .cli import main as full_main

    return full_main(arguments, use_default_usage_store=True)
