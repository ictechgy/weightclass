"""Run weightclass as `python -m weightclass`, equivalent to the `wclass` command."""

from .entrypoint import main

if __name__ == "__main__":
    raise SystemExit(main())
