#!/usr/bin/env python3
"""Seal and validate a task-free advisory measurement campaign contract.

The manifest binds only reviewed configuration: route digests, verifier bytes,
pricing basis, sample bounds, and the already-fixed advisory decision rule. It
never contains task text, task hashes, repository paths, timestamps, profiles,
or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import stat
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, TypedDict, cast

if TYPE_CHECKING:
    from tools.advisory_routes import AdvisoryRouteError, routes_from_profile
else:
    from advisory_routes import AdvisoryRouteError, routes_from_profile

CAMPAIGN_SCHEMA_VERSION = 1
MINIMUM_ADVISED_FAILURES = 12
MAX_CAMPAIGN_BYTES = 16_384
MAX_VERIFY_BYTES = 1_048_576
MAX_PRICES_BYTES = 65_536
MAX_CAMPAIGN_LOG_BYTES = 67_108_864
MAX_TASKS = 500


class CampaignError(ValueError):
    """Value-free rejection of an invalid or mismatched campaign contract."""


class RouteContract(TypedDict):
    executable: str
    argv_sha256: str
    argv_count: int


class CampaignManifest(TypedDict):
    schema_version: int
    arm: str
    planned_tasks: int
    max_tasks: int
    minimum_advised_failures: int
    early_stop: str
    primary_endpoint: str
    decision_rule: str
    cost_basis: str
    routes: dict[str, RouteContract]
    advisor_context: str
    verify_sha256: str
    prices_sha256: str | None
    campaign_fingerprint: str


class CampaignProgress(NamedTuple):
    usable_tasks: int
    advised_failures: int
    decision_eligible: bool
    reached_cap: bool
    reason: str


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CampaignError()
        result[key] = value
    return result


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise CampaignError() from error


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _bounded_file_bytes(path: Path, maximum: int) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise CampaignError()
    flags = os.O_RDONLY | nofollow
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CampaignError()
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as error:
        raise CampaignError() from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    payload = b"".join(chunks)
    if len(payload) > maximum:
        raise CampaignError()
    return payload


def file_sha256(path: Path, maximum: int) -> str:
    return _sha256(_bounded_file_bytes(path, maximum))


def stage_bound_file(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    maximum: int,
    mode: int,
) -> Path:
    payload = _bounded_file_bytes(source, maximum)
    if _sha256(payload) != expected_sha256:
        raise CampaignError()
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        if destination.exists() or destination.is_symlink():
            if _bounded_file_bytes(destination, maximum) != payload:
                raise CampaignError()
            os.chmod(destination, mode, follow_symlinks=False)
            return destination
        descriptor = os.open(destination, flags, mode)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CampaignError()
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    except OSError as error:
        raise CampaignError() from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    directory_descriptor = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return destination


def price_table_sha256(path: Path) -> str:
    payload = _bounded_file_bytes(path, MAX_PRICES_BYTES)
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(CampaignError()),
        )
    except (UnicodeDecodeError, ValueError, RecursionError, CampaignError) as error:
        raise CampaignError() from error
    if not isinstance(value, Mapping) or set(value) != {"cheap", "expensive", "advisor"}:
        raise CampaignError()
    for arm in ("cheap", "expensive", "advisor"):
        table = value[arm]
        if not isinstance(table, Mapping) or not table:
            raise CampaignError()
        for field, rate in table.items():
            if (
                not isinstance(field, str)
                or not field
                or any(character.isspace() or not character.isprintable() for character in field)
                or isinstance(rate, bool)
                or not isinstance(rate, (int, float))
            ):
                raise CampaignError()
            try:
                numeric = float(rate)
            except OverflowError as error:
                raise CampaignError() from error
            if not math.isfinite(numeric) or numeric < 0:
                raise CampaignError()
    return _sha256(payload)


def route_contract(argv: Sequence[str]) -> RouteContract:
    if not argv or any(not isinstance(token, str) or not token for token in argv):
        raise CampaignError()
    try:
        encoded = "\0".join(argv).encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise CampaignError() from error
    if len(encoded) > 65_536:
        raise CampaignError()
    return {
        "executable": Path(argv[0]).name,
        "argv_sha256": _sha256(encoded),
        "argv_count": len(argv) - 1,
    }


def _integer(value: object, lower: int, upper: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise CampaignError()
    return value


def _string(value: object, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise CampaignError()
    return value


def _digest(value: object, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise CampaignError()
    return value


def _route(value: object) -> RouteContract:
    if not isinstance(value, Mapping) or set(value) != {
        "executable",
        "argv_sha256",
        "argv_count",
    }:
        raise CampaignError()
    executable = value["executable"]
    if not isinstance(executable, str):
        raise CampaignError()
    try:
        encoded_size = len(executable.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as error:
        raise CampaignError() from error
    if (
        not 1 <= encoded_size <= 255
        or executable != Path(executable).name
        or executable != executable.strip(" ")
        or any(
            not character.isprintable() or (character.isspace() and character != " ")
            for character in executable
        )
    ):
        raise CampaignError()
    return {
        "executable": executable,
        "argv_sha256": cast(str, _digest(value["argv_sha256"])),
        "argv_count": _integer(value["argv_count"], 0, 128),
    }


def _manifest_payload(raw: object) -> CampaignManifest:
    expected = {
        "schema_version",
        "arm",
        "planned_tasks",
        "max_tasks",
        "minimum_advised_failures",
        "early_stop",
        "primary_endpoint",
        "decision_rule",
        "cost_basis",
        "routes",
        "advisor_context",
        "verify_sha256",
        "prices_sha256",
        "campaign_fingerprint",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise CampaignError()
    if _integer(raw["schema_version"], 1, 1) != CAMPAIGN_SCHEMA_VERSION:
        raise CampaignError()
    arm = _string(raw["arm"], frozenset({"shape_b", "shape_a_b"}))
    planned_tasks = _integer(raw["planned_tasks"], MINIMUM_ADVISED_FAILURES, MAX_TASKS)
    max_tasks = _integer(raw["max_tasks"], planned_tasks, MAX_TASKS)
    minimum = _integer(
        raw["minimum_advised_failures"],
        MINIMUM_ADVISED_FAILURES,
        MINIMUM_ADVISED_FAILURES,
    )
    early_stop = _string(
        raw["early_stop"],
        frozenset({"minimums_then_decisive_interval_or_max_tasks"}),
    )
    endpoint = _string(raw["primary_endpoint"], frozenset({"cost_per_passing_task"}))
    expected_rule = "s_interval_gt_a_plus_qr" if arm == "shape_b" else "paired_a_b_intervals"
    decision_rule = _string(raw["decision_rule"], frozenset({expected_rule}))
    cost_basis = _string(raw["cost_basis"], frozenset({"price_table", "vendor"}))
    routes = raw["routes"]
    if not isinstance(routes, Mapping) or set(routes) != {"cheap", "expensive", "advisor"}:
        raise CampaignError()
    route_values = {name: _route(routes[name]) for name in ("cheap", "expensive", "advisor")}
    if (
        cost_basis == "vendor"
        and len({route["executable"] for route in route_values.values()}) != 1
    ):
        raise CampaignError()
    context = _string(raw["advisor_context"], frozenset({"prompt", "repo"}))
    verify_digest = cast(str, _digest(raw["verify_sha256"]))
    prices_digest = _digest(raw["prices_sha256"], nullable=True)
    if (cost_basis == "price_table") != (prices_digest is not None):
        raise CampaignError()
    fingerprint = cast(str, _digest(raw["campaign_fingerprint"]))
    manifest: CampaignManifest = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "arm": arm,
        "planned_tasks": planned_tasks,
        "max_tasks": max_tasks,
        "minimum_advised_failures": minimum,
        "early_stop": early_stop,
        "primary_endpoint": endpoint,
        "decision_rule": decision_rule,
        "cost_basis": cost_basis,
        "routes": route_values,
        "advisor_context": context,
        "verify_sha256": verify_digest,
        "prices_sha256": prices_digest,
        "campaign_fingerprint": fingerprint,
    }
    unsigned = dict(manifest)
    del unsigned["campaign_fingerprint"]
    if _sha256(_canonical_bytes(unsigned)) != fingerprint:
        raise CampaignError()
    return manifest


def load_manifest(path: Path) -> CampaignManifest:
    payload = _bounded_file_bytes(path, MAX_CAMPAIGN_BYTES)
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(CampaignError()),
        )
    except (UnicodeDecodeError, ValueError, RecursionError, CampaignError) as error:
        raise CampaignError() from error
    return _manifest_payload(value)


def load_bound_records(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    payload = _bounded_file_bytes(path, MAX_CAMPAIGN_LOG_BYTES)
    records: list[dict[str, object]] = []
    for raw_line in payload.splitlines():
        if not raw_line.strip():
            continue
        try:
            value = json.loads(
                raw_line.decode("utf-8", errors="strict"),
                object_pairs_hook=_object_without_duplicates,
                parse_constant=lambda _value: (_ for _ in ()).throw(CampaignError()),
            )
        except (UnicodeDecodeError, ValueError, RecursionError, CampaignError) as error:
            raise CampaignError() from error
        if not isinstance(value, dict):
            raise CampaignError()
        records.append(value)
    return records


def build_manifest(
    *,
    arm: str,
    planned_tasks: int,
    max_tasks: int,
    cost_basis: str,
    cheap: Sequence[str],
    expensive: Sequence[str],
    advisor: Sequence[str],
    advisor_context: str,
    verify: Path,
    prices: Path | None,
) -> CampaignManifest:
    unsigned: dict[str, object] = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "arm": arm,
        "planned_tasks": planned_tasks,
        "max_tasks": max_tasks,
        "minimum_advised_failures": MINIMUM_ADVISED_FAILURES,
        "early_stop": "minimums_then_decisive_interval_or_max_tasks",
        "primary_endpoint": "cost_per_passing_task",
        "decision_rule": (
            "s_interval_gt_a_plus_qr" if arm == "shape_b" else "paired_a_b_intervals"
        ),
        "cost_basis": cost_basis,
        "routes": {
            "cheap": route_contract(cheap),
            "expensive": route_contract(expensive),
            "advisor": route_contract(advisor),
        },
        "advisor_context": advisor_context,
        "verify_sha256": file_sha256(verify, MAX_VERIFY_BYTES),
        "prices_sha256": price_table_sha256(prices) if prices is not None else None,
    }
    raw = dict(unsigned)
    raw["campaign_fingerprint"] = _sha256(_canonical_bytes(unsigned))
    return _manifest_payload(raw)


def canonical_manifest_bytes(manifest: CampaignManifest) -> bytes:
    return _canonical_bytes(manifest) + b"\n"


def write_manifest(path: Path, manifest: CampaignManifest) -> None:
    if path.exists() or path.is_symlink():
        raise CampaignError()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".campaign-", dir=path.parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(canonical_manifest_bytes(manifest))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        temporary = None
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        raise CampaignError() from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def validate_run_configuration(
    manifest: CampaignManifest,
    *,
    cheap: Sequence[str],
    expensive: Sequence[str],
    advisor: Sequence[str],
    advise_first: bool,
    advise_on_failure: bool,
    advisor_context: str,
    verify: Path,
    prices: Path | None,
    prefer_prices: bool,
    sample_ordinal: int,
) -> None:
    expected_flags = (False, True) if manifest["arm"] == "shape_b" else (True, True)
    if (advise_first, advise_on_failure) != expected_flags:
        raise CampaignError()
    if manifest["routes"] != {
        "cheap": route_contract(cheap),
        "expensive": route_contract(expensive),
        "advisor": route_contract(advisor),
    }:
        raise CampaignError()
    if manifest["advisor_context"] != advisor_context:
        raise CampaignError()
    if manifest["verify_sha256"] != file_sha256(verify, MAX_VERIFY_BYTES):
        raise CampaignError()
    if manifest["cost_basis"] == "price_table":
        if prices is None or not prefer_prices:
            raise CampaignError()
        if manifest["prices_sha256"] != price_table_sha256(prices):
            raise CampaignError()
    elif prices is not None or prefer_prices:
        raise CampaignError()
    _integer(sample_ordinal, 1, manifest["max_tasks"])


def record_binding(manifest: CampaignManifest, sample_ordinal: int) -> dict[str, object]:
    _integer(sample_ordinal, 1, manifest["max_tasks"])
    return {
        "campaign_fingerprint": manifest["campaign_fingerprint"],
        "arm": manifest["arm"],
        "sample_ordinal": sample_ordinal,
    }


def validate_record_bindings(
    manifest: CampaignManifest, records: Sequence[Mapping[str, object]]
) -> set[int]:
    ordinals: set[int] = set()
    for record in records:
        binding = record.get("campaign")
        if not isinstance(binding, Mapping) or set(binding) != {
            "campaign_fingerprint",
            "arm",
            "sample_ordinal",
        }:
            raise CampaignError()
        if (
            binding["campaign_fingerprint"] != manifest["campaign_fingerprint"]
            or binding["arm"] != manifest["arm"]
        ):
            raise CampaignError()
        ordinal = _integer(binding["sample_ordinal"], 1, manifest["max_tasks"])
        if ordinal in ordinals:
            raise CampaignError()
        ordinals.add(ordinal)
    if len(ordinals) > manifest["max_tasks"]:
        raise CampaignError()
    if ordinals != set(range(1, len(ordinals) + 1)):
        raise CampaignError()
    return ordinals


def campaign_progress(
    manifest: CampaignManifest, records: Sequence[Mapping[str, object]]
) -> CampaignProgress:
    ordinals = validate_record_bindings(manifest, records)
    usable = []
    for record in records:
        cheap = record.get("cheap")
        if isinstance(cheap, Mapping) and cheap.get("failure_kind") == "infrastructure":
            continue
        usable.append(record)
    advised_failures = sum(1 for record in usable if isinstance(record.get("advice_failure"), dict))
    total = len(usable)
    eligible = (
        total >= manifest["planned_tasks"]
        and advised_failures >= manifest["minimum_advised_failures"]
    )
    reached_cap = len(ordinals) >= manifest["max_tasks"]
    if eligible:
        reason = "minimums_met"
    elif reached_cap:
        reason = "maximum_reached_without_minimums"
    elif total < manifest["planned_tasks"]:
        reason = "planned_tasks_not_reached"
    else:
        reason = "advised_failures_not_reached"
    return CampaignProgress(total, advised_failures, eligible, reached_cap, reason)


def _command(value: str) -> list[str]:
    try:
        argv = shlex.split(value)
    except ValueError as error:
        raise CampaignError() from error
    if not argv:
        raise CampaignError()
    return argv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, choices=("shape_b", "shape_a_b"))
    parser.add_argument("--planned-tasks", required=True, type=int)
    parser.add_argument("--max-tasks", required=True, type=int)
    parser.add_argument("--cost-basis", required=True, choices=("price_table", "vendor"))
    parser.add_argument("--cheap")
    parser.add_argument("--expensive")
    parser.add_argument("--advisor")
    parser.add_argument(
        "--route-profile",
        type=Path,
        help=(
            "task-free Claude/Codex model-and-effort profile; mutually exclusive "
            "with --cheap/--advisor/--expensive"
        ),
    )
    parser.add_argument("--advisor-context", choices=("prompt", "repo"), default="prompt")
    parser.add_argument("--verify", required=True, type=Path)
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    if (arguments.cost_basis == "price_table") != (arguments.prices is not None):
        parser.error("price_table cost basis requires exactly one --prices file")
    exact_commands = (arguments.cheap, arguments.advisor, arguments.expensive)
    try:
        if arguments.route_profile is None:
            if any(command is None for command in exact_commands):
                parser.error("supply all exact route commands or one --route-profile")
            cheap = _command(cast(str, arguments.cheap))
            advisor = _command(cast(str, arguments.advisor))
            expensive = _command(cast(str, arguments.expensive))
        else:
            if any(command is not None for command in exact_commands):
                parser.error("--route-profile cannot be mixed with exact route commands")
            routes = routes_from_profile(arguments.route_profile.expanduser())
            cheap = list(routes.cheap)
            advisor = list(routes.advisor)
            expensive = list(routes.expensive)
    except (AdvisoryRouteError, CampaignError):
        parser.error("invalid advisory routes")
    try:
        manifest = build_manifest(
            arm=arguments.arm,
            planned_tasks=arguments.planned_tasks,
            max_tasks=arguments.max_tasks,
            cost_basis=arguments.cost_basis,
            cheap=cheap,
            expensive=expensive,
            advisor=advisor,
            advisor_context=arguments.advisor_context,
            verify=arguments.verify.expanduser().resolve(),
            prices=arguments.prices.expanduser().resolve() if arguments.prices else None,
        )
        write_manifest(arguments.output.expanduser().resolve(), manifest)
    except CampaignError:
        parser.error("invalid campaign configuration")
    print(manifest["campaign_fingerprint"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
