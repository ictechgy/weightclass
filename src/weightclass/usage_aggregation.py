"""Opt-in aggregate-only native usage accounting."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import sys
import tempfile
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from .adapter_registry import BUILT_IN_AGENT_IDS

STORE_SCHEMA_VERSION: Final = 1
MAX_STORE_BYTES: Final = 262_144
MAX_WEIGHTS: Final = 1_024
MAX_BUCKETS: Final = 4_096
MAX_COUNTER: Final = (1 << 63) - 1
WEIGHT_SCALE: Final = 1_000_000
MAX_WEIGHT_MICROS: Final = 1_000 * WEIGHT_SCALE
_STORE_KEYS: Final = {"aggregate_only", "buckets", "coverage", "schema_version", "weights"}
_WEIGHT_KEYS: Final = {"agent", "model", "effort", "relative_cost_micros"}
_BUCKET_KEYS: Final = {
    "agent",
    "effort",
    "escalations",
    "failed",
    "lower_weight_runs",
    "model",
    "relative_cost_micros_total",
    "reworks",
    "runs",
    "status_counts",
    "succeeded",
    "tier",
    "unweighted_runs",
    "weighted_runs",
}


class UsageAggregationError(ValueError):
    """A value-free aggregate store failure."""


@dataclass(frozen=True, slots=True)
class UsageDimensions:
    agent: str
    model: str | None
    effort: str
    tier: str


def _label(value: object, *, maximum_bytes: int) -> str:
    if not isinstance(value, str):
        raise UsageAggregationError()
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise UsageAggregationError() from None
    if not 1 <= len(encoded) <= maximum_bytes or any(
        character.isspace()
        or not character.isprintable()
        or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise UsageAggregationError()
    return value


def _agent(value: object) -> str:
    selected = _label(value, maximum_bytes=64)
    if selected not in BUILT_IN_AGENT_IDS:
        raise UsageAggregationError()
    return selected


def _model(value: object) -> str | None:
    return None if value is None else _label(value, maximum_bytes=240)


def _effort(value: object) -> str:
    selected = _label(value, maximum_bytes=64)
    if selected not in {"low", "medium", "high"}:
        raise UsageAggregationError()
    return selected


def _tier(value: object) -> str:
    selected = _label(value, maximum_bytes=64)
    if selected not in {"low", "standard", "high"}:
        raise UsageAggregationError()
    return selected


def _counter(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_COUNTER:
        raise UsageAggregationError()
    return value


def _increment(value: int, amount: int = 1) -> int:
    result = value + amount
    if result > MAX_COUNTER:
        raise UsageAggregationError()
    return result


def _relative_cost_micros(value: object) -> int:
    if not isinstance(value, str) or value != value.strip():
        raise UsageAggregationError()
    try:
        decimal_value = Decimal(value)
    except InvalidOperation:
        raise UsageAggregationError() from None
    scaled = decimal_value * WEIGHT_SCALE
    if (
        not decimal_value.is_finite()
        or decimal_value <= 0
        or scaled != scaled.to_integral_value()
        or scaled > MAX_WEIGHT_MICROS
    ):
        raise UsageAggregationError()
    return int(scaled)


def _status_key(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or not -255 <= value <= 255:
        raise UsageAggregationError()
    return f"exit:{value}" if value >= 0 else f"signal:{-value}"


def _validate_status_counts(value: object, runs: int) -> dict[str, int]:
    if not isinstance(value, dict) or len(value) > 511:
        raise UsageAggregationError()
    result: dict[str, int] = {}
    for key, count_value in value.items():
        if not isinstance(key, str):
            raise UsageAggregationError()
        prefix, separator, number = key.partition(":")
        if separator != ":" or prefix not in {"exit", "signal"} or not number.isascii():
            raise UsageAggregationError()
        try:
            parsed = int(number)
        except ValueError:
            raise UsageAggregationError() from None
        if not 0 <= parsed <= 255 or (prefix == "signal" and parsed == 0):
            raise UsageAggregationError()
        result[key] = _counter(count_value)
    if sum(result.values()) != runs:
        raise UsageAggregationError()
    return result


def _validate_weight(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _WEIGHT_KEYS:
        raise UsageAggregationError()
    relative_cost = _counter(value["relative_cost_micros"])
    if not 1 <= relative_cost <= MAX_WEIGHT_MICROS:
        raise UsageAggregationError()
    return {
        "agent": _agent(value["agent"]),
        "model": _model(value["model"]),
        "effort": _effort(value["effort"]),
        "relative_cost_micros": relative_cost,
    }


def _validate_bucket(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _BUCKET_KEYS:
        raise UsageAggregationError()
    runs = _counter(value["runs"])
    succeeded = _counter(value["succeeded"])
    failed = _counter(value["failed"])
    weighted_runs = _counter(value["weighted_runs"])
    unweighted_runs = _counter(value["unweighted_runs"])
    lower_weight_runs = _counter(value["lower_weight_runs"])
    reworks = _counter(value["reworks"])
    escalations = _counter(value["escalations"])
    relative_cost_total = _counter(value["relative_cost_micros_total"])
    if (
        succeeded + failed != runs
        or weighted_runs + unweighted_runs != runs
        or lower_weight_runs > weighted_runs
        or reworks > runs
        or escalations > runs
        or (weighted_runs == 0) != (relative_cost_total == 0)
    ):
        raise UsageAggregationError()
    return {
        "agent": _agent(value["agent"]),
        "effort": _effort(value["effort"]),
        "escalations": escalations,
        "failed": failed,
        "lower_weight_runs": lower_weight_runs,
        "model": _model(value["model"]),
        "relative_cost_micros_total": relative_cost_total,
        "reworks": reworks,
        "runs": runs,
        "status_counts": _validate_status_counts(value["status_counts"], runs),
        "succeeded": succeeded,
        "tier": _tier(value["tier"]),
        "unweighted_runs": unweighted_runs,
        "weighted_runs": weighted_runs,
    }


def _weight_key(value: Mapping[str, object]) -> tuple[str, str, str]:
    model = value["model"]
    assert model is None or isinstance(model, str)
    return str(value["agent"]), model or "", str(value["effort"])


def _bucket_key(value: Mapping[str, object]) -> tuple[str, str, str, str]:
    return (*_weight_key(value), str(value["tier"]))


def _validate_store(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _STORE_KEYS:
        raise UsageAggregationError()
    if (
        value["aggregate_only"] is not True
        or value["coverage"] != "native_schema_3"
        or value["schema_version"] != STORE_SCHEMA_VERSION
        or isinstance(value["schema_version"], bool)
    ):
        raise UsageAggregationError()
    raw_weights = value["weights"]
    raw_buckets = value["buckets"]
    if (
        not isinstance(raw_weights, list)
        or len(raw_weights) > MAX_WEIGHTS
        or not isinstance(raw_buckets, list)
        or len(raw_buckets) > MAX_BUCKETS
    ):
        raise UsageAggregationError()
    weights = [_validate_weight(item) for item in raw_weights]
    buckets = [_validate_bucket(item) for item in raw_buckets]
    if len({_weight_key(item) for item in weights}) != len(weights):
        raise UsageAggregationError()
    if len({_bucket_key(item) for item in buckets}) != len(buckets):
        raise UsageAggregationError()
    weights.sort(key=_weight_key)
    buckets.sort(key=_bucket_key)
    return {
        "aggregate_only": True,
        "buckets": buckets,
        "coverage": "native_schema_3",
        "schema_version": STORE_SCHEMA_VERSION,
        "weights": weights,
    }


def _empty_store() -> dict[str, object]:
    return {
        "aggregate_only": True,
        "buckets": [],
        "coverage": "native_schema_3",
        "schema_version": STORE_SCHEMA_VERSION,
        "weights": [],
    }


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
        + b"\n"
    )


def _require_private_regular(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise UsageAggregationError()


def _require_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise UsageAggregationError() from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise UsageAggregationError()


def _read_descriptor(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = os.read(descriptor, min(65_536, MAX_STORE_BYTES + 1 - size))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_STORE_BYTES:
            raise UsageAggregationError()


def _load_store(path: Path) -> dict[str, object]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        raise UsageAggregationError() from None
    try:
        _require_private_regular(descriptor)
        payload = _read_descriptor(descriptor)
    except (OSError, UsageAggregationError):
        raise UsageAggregationError() from None
    finally:
        os.close(descriptor)
    try:
        return _validate_store(json.loads(payload.decode("ascii")))
    except (UnicodeDecodeError, json.JSONDecodeError, UsageAggregationError):
        raise UsageAggregationError() from None
    except RecursionError:
        # 깊게 중첩된 JSON 은 JSONDecodeError 가 아니라 RecursionError 로 끝난다.
        # RecursionError 는 ValueError 가 아니라 RuntimeError 계열이라 위 튜플에
        # 걸리지 않고, 상한(MAX_STORE_BYTES) 안에서도 재귀 한도를 넘길 수 있다.
        # 잡지 않으면 적대적 저장소 하나가 라우터를 진단 없는 크래시로 끝낸다.
        # CPython 버전에 따라 한도가 달라 3.10/3.11 에서만 드러났다.
        raise UsageAggregationError() from None


def _write_descriptor(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise UsageAggregationError()
        written += count
    os.fsync(descriptor)


def _replace_store(path: Path, value: object) -> None:
    payload = _canonical_bytes(_validate_store(value))
    if len(payload) > MAX_STORE_BYTES:
        raise UsageAggregationError()
    temporary_descriptor = -1
    temporary_name = ""
    try:
        temporary_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".weightclass-usage-",
            dir=path.parent,
        )
        os.fchmod(temporary_descriptor, 0o600)
        _write_descriptor(temporary_descriptor, payload)
        os.close(temporary_descriptor)
        temporary_descriptor = -1
        os.replace(temporary_name, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except (OSError, UsageAggregationError):
        raise UsageAggregationError() from None
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            except OSError:
                raise UsageAggregationError() from None


@contextmanager
def _locked(path: Path, *, exclusive: bool) -> Iterator[None]:
    lock_path = Path(f"{path}.lock")
    try:
        _require_private_directory(path.parent)
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        _require_private_regular(descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    except (OSError, UsageAggregationError):
        if "descriptor" in locals():
            os.close(descriptor)
        raise UsageAggregationError() from None
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _update_store(path: Path, update: Callable[[dict[str, object]], None]) -> None:
    with _locked(path, exclusive=True):
        value = _load_store(path)
        update(value)
        _replace_store(path, value)


def ensure_usage_store(path: Path) -> None:
    """Create or validate one private aggregate-only store."""
    if not path.is_absolute():
        raise UsageAggregationError()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        raise UsageAggregationError() from None
    with _locked(path, exclusive=True):
        try:
            path.lstat()
        except FileNotFoundError:
            _replace_store(path, _empty_store())
        except OSError:
            raise UsageAggregationError() from None
        else:
            _load_store(path)


def set_relative_cost_weight(
    path: Path,
    agent: str,
    model: str | None,
    effort: str,
    relative_cost: str,
) -> dict[str, object]:
    weight: dict[str, object] = {
        "agent": _agent(agent),
        "model": _model(model),
        "effort": _effort(effort),
        "relative_cost_micros": _relative_cost_micros(relative_cost),
    }

    def update(value: dict[str, object]) -> None:
        weights = value["weights"]
        assert isinstance(weights, list)
        key = _weight_key(weight)
        weights[:] = [item for item in weights if _weight_key(item) != key]
        weights.append(weight)

    _update_store(path, update)
    return {
        "agent": weight["agent"],
        "effort": weight["effort"],
        "model": weight["model"],
        "relative_cost": (
            f"{Decimal(_counter(weight['relative_cost_micros'])) / WEIGHT_SCALE:.6f}"
        ),
        "schema_version": STORE_SCHEMA_VERSION,
    }


def record_usage(
    path: Path,
    dimensions: UsageDimensions,
    *,
    child_returncode: int,
    rework: bool,
    escalation: bool,
) -> None:
    if not isinstance(rework, bool) or not isinstance(escalation, bool):
        raise UsageAggregationError()
    normalized: dict[str, object] = {
        "agent": _agent(dimensions.agent),
        "model": _model(dimensions.model),
        "effort": _effort(dimensions.effort),
        "tier": _tier(dimensions.tier),
    }
    status = _status_key(child_returncode)

    def update(value: dict[str, object]) -> None:
        weights = value["weights"]
        buckets = value["buckets"]
        assert isinstance(weights, list)
        assert isinstance(buckets, list)
        weight = next(
            (
                _counter(item["relative_cost_micros"])
                for item in weights
                if _weight_key(item) == _weight_key(normalized)
            ),
            None,
        )
        key = _bucket_key(normalized)
        bucket = next((item for item in buckets if _bucket_key(item) == key), None)
        if bucket is None:
            if len(buckets) >= MAX_BUCKETS:
                raise UsageAggregationError()
            bucket = {
                **normalized,
                "escalations": 0,
                "failed": 0,
                "lower_weight_runs": 0,
                "relative_cost_micros_total": 0,
                "reworks": 0,
                "runs": 0,
                "status_counts": {},
                "succeeded": 0,
                "unweighted_runs": 0,
                "weighted_runs": 0,
            }
            buckets.append(bucket)
        bucket["runs"] = _increment(int(bucket["runs"]))
        outcome_key = "succeeded" if child_returncode == 0 else "failed"
        bucket[outcome_key] = _increment(int(bucket[outcome_key]))
        bucket["reworks"] = _increment(int(bucket["reworks"]), int(rework))
        bucket["escalations"] = _increment(int(bucket["escalations"]), int(escalation))
        status_counts = bucket["status_counts"]
        assert isinstance(status_counts, dict)
        status_counts[status] = _increment(int(status_counts.get(status, 0)))
        if weight is None:
            bucket["unweighted_runs"] = _increment(int(bucket["unweighted_runs"]))
        else:
            bucket["weighted_runs"] = _increment(int(bucket["weighted_runs"]))
            bucket["relative_cost_micros_total"] = _increment(
                int(bucket["relative_cost_micros_total"]), weight
            )
            if weight < WEIGHT_SCALE:
                bucket["lower_weight_runs"] = _increment(int(bucket["lower_weight_runs"]))

    _update_store(path, update)


def render_usage_report(path: Path) -> dict[str, object]:
    with _locked(path, exclusive=False):
        value = _load_store(path)
    buckets = value["buckets"]
    weights = value["weights"]
    assert isinstance(buckets, list)
    assert isinstance(weights, list)
    totals: dict[str, object] = {
        "escalations": 0,
        "failed": 0,
        "lower_weight_runs": 0,
        "relative_cost_micros_total": 0,
        "reworks": 0,
        "runs": 0,
        "status_counts": {},
        "succeeded": 0,
        "unweighted_runs": 0,
        "weighted_runs": 0,
    }
    for bucket in buckets:
        for key in (
            "escalations",
            "failed",
            "lower_weight_runs",
            "relative_cost_micros_total",
            "reworks",
            "runs",
            "succeeded",
            "unweighted_runs",
            "weighted_runs",
        ):
            totals[key] = _increment(_counter(totals[key]), _counter(bucket[key]))
        total_statuses = totals["status_counts"]
        bucket_statuses = bucket["status_counts"]
        assert isinstance(total_statuses, dict)
        assert isinstance(bucket_statuses, dict)
        for status, count in bucket_statuses.items():
            total_statuses[status] = _increment(int(total_statuses.get(status, 0)), int(count))
    return {
        "aggregate_only": True,
        "buckets": [_render_metrics(bucket) | _dimensions(bucket) for bucket in buckets],
        "claims": {
            "pricing_verified": False,
            "relative_cost_only": True,
            "rework_self_reported": True,
            "task_content_recorded": False,
            "weights_apply_prospectively": True,
        },
        "coverage": "native_schema_3",
        "schema_version": STORE_SCHEMA_VERSION,
        "totals": _render_metrics(totals),
        "weights": [_render_weight(weight) for weight in weights],
    }


def resolve_usage_store(
    explicit_path: Path | None,
    *,
    use_default: bool,
    rework: bool,
    escalation: bool,
) -> Path | None:
    """Resolve an enabled store without creating state during execution."""
    if explicit_path is None and not use_default:
        if rework or escalation:
            raise UsageAggregationError()
        return None
    path = explicit_path if explicit_path is not None else default_usage_store_path()
    if not path.is_absolute():
        raise UsageAggregationError()
    try:
        path.lstat()
    except FileNotFoundError:
        if explicit_path is not None or rework or escalation:
            raise UsageAggregationError() from None
        return None
    except OSError:
        raise UsageAggregationError() from None
    with _locked(path, exclusive=False):
        _load_store(path)
    return path


def _dimensions(value: dict[str, object]) -> dict[str, object]:
    return {key: value[key] for key in ("agent", "effort", "model", "tier")}


def _render_weight(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "agent": value["agent"],
        "effort": value["effort"],
        "model": value["model"],
        "relative_cost": (f"{Decimal(_counter(value['relative_cost_micros'])) / WEIGHT_SCALE:.6f}"),
    }


def _ratio(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return f"{Decimal(numerator) / Decimal(denominator):.6f}"


def _cost_units(value: int, weighted_runs: int) -> str | None:
    if weighted_runs == 0:
        return None
    return f"{Decimal(value) / Decimal(WEIGHT_SCALE):.6f}"


def _relative_cost_comparison(
    value: int,
    weighted_runs: int,
) -> tuple[str | None, str | None, str | None]:
    if weighted_runs == 0:
        return None, None, None
    baseline_micros = weighted_runs * WEIGHT_SCALE
    savings_micros = baseline_micros - value
    return (
        f"{Decimal(baseline_micros) / Decimal(WEIGHT_SCALE):.6f}",
        f"{Decimal(savings_micros) / Decimal(baseline_micros):.6f}",
        f"{Decimal(savings_micros) / Decimal(WEIGHT_SCALE):.6f}",
    )


def _render_metrics(value: Mapping[str, object]) -> dict[str, object]:
    runs = _counter(value["runs"])
    weighted_runs = _counter(value["weighted_runs"])
    relative_cost_micros = _counter(value["relative_cost_micros_total"])
    baseline_units, savings_ratio, savings_units = _relative_cost_comparison(
        relative_cost_micros,
        weighted_runs,
    )
    return {
        "escalation_ratio": _ratio(_counter(value["escalations"]), runs),
        "escalations": value["escalations"],
        "failed": value["failed"],
        "lower_weight_ratio": _ratio(_counter(value["lower_weight_runs"]), weighted_runs),
        "lower_weight_runs": value["lower_weight_runs"],
        "relative_cost_baseline_units": baseline_units,
        "relative_cost_savings_ratio": savings_ratio,
        "relative_cost_savings_units": savings_units,
        "relative_cost_units": _cost_units(relative_cost_micros, weighted_runs),
        "rework_ratio": _ratio(_counter(value["reworks"]), runs),
        "reworks": value["reworks"],
        "runs": runs,
        "status_counts": value["status_counts"],
        "succeeded": value["succeeded"],
        "unweighted_runs": value["unweighted_runs"],
        "weighted_runs": weighted_runs,
    }


def default_usage_store_path() -> Path:
    """Return the platform-local aggregate store without creating it."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "weightclass" / "usage-v1.json"
    state_home = os.environ.get("XDG_STATE_HOME")
    root = (
        Path(state_home)
        if state_home and Path(state_home).is_absolute()
        else Path.home() / ".local/state"
    )
    return root / "weightclass" / "usage-v1.json"
