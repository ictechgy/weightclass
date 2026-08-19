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
from .json_input import json_object_pairs_without_duplicates

STORE_SCHEMA_VERSION: Final = 2
MAX_STORE_BYTES: Final = 262_144
MAX_WEIGHTS: Final = 1_024
MAX_BUCKETS: Final = 4_096
MAX_COUNTER: Final = (1 << 63) - 1
WEIGHT_SCALE: Final = 1_000_000
MAX_WEIGHT_MICROS: Final = 1_000 * WEIGHT_SCALE

# 라우팅을 하지 않았다면 태스크가 갔을 고정 경로의 노력 수준. 내장 standard
# 라우트가 쓰는 값과 같다.
#
# 스키마 1 은 반사실 없이 "실행 1건당 1.0" 을 기준선으로 삼았다. 그 값은 사용자가
# 입력한 가중치의 항등식이어서, 같은 실행 이력에 가중치만 바꾸면 절감률이 10% 도
# 90% 도 되었고 반증할 수 없었다. 더 나쁜 것은 재작업이 기준선까지 함께 부풀린
# 점이다. low(0.3) 10건 중 5건이 실패해 high(2.0) 로 다시 돈 경우 실제 지출은
# 13.0 단위, 기본 경로는 10.0 단위로 30% 초과인데 리포트는 13.3% 절감을 냈다.
# 실패한 저비용 라우팅이 절감으로 보이는 방향의 오류였다.
BASELINE_EFFORT: Final = "medium"

_STORE_KEYS_V1: Final = {"aggregate_only", "buckets", "coverage", "schema_version", "weights"}
_STORE_KEYS: Final = _STORE_KEYS_V1 | {"baseline"}
_BASELINE_KEYS: Final = {"counted_tasks", "relative_cost_micros_total", "tasks"}
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


def _recorded_tasks(buckets: list[dict[str, object]]) -> int:
    """Return how many first attempts the buckets account for.

    재작업은 새 태스크가 아니다. 태스크 수를 실행 수와 구분해야 재작업 비용이
    기준선까지 부풀리는 일이 없다.
    """
    tasks = 0
    for bucket in buckets:
        tasks = _increment(tasks, _counter(bucket["runs"]) - _counter(bucket["reworks"]))
    return tasks


def _validate_baseline(value: object, tasks: int) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _BASELINE_KEYS:
        raise UsageAggregationError()
    recorded_tasks = _counter(value["tasks"])
    counted_tasks = _counter(value["counted_tasks"])
    total = _counter(value["relative_cost_micros_total"])
    if (
        recorded_tasks != tasks
        or counted_tasks > recorded_tasks
        or (counted_tasks == 0) != (total == 0)
    ):
        raise UsageAggregationError()
    return {
        "counted_tasks": counted_tasks,
        "relative_cost_micros_total": total,
        "tasks": recorded_tasks,
    }


def _validate_store(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise UsageAggregationError()
    version = value.get("schema_version")
    if isinstance(version, bool) or version not in (1, STORE_SCHEMA_VERSION):
        raise UsageAggregationError()
    expected_keys = _STORE_KEYS_V1 if version == 1 else _STORE_KEYS
    if set(value) != expected_keys:
        raise UsageAggregationError()
    if value["aggregate_only"] is not True or value["coverage"] != "native_schema_3":
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
    tasks = _recorded_tasks(buckets)
    # 스키마 1 은 기준선을 기록하지 않았다. 승격된 저장소는 태스크 수만 되살리고
    # 기준선 증거는 0 으로 둔다. 없는 증거를 만들어 내지 않기 위해서이며, 그러면
    # 리포트가 missing_baseline_weight 로 절감 계산을 스스로 기권한다.
    baseline = (
        {"counted_tasks": 0, "relative_cost_micros_total": 0, "tasks": tasks}
        if version == 1
        else _validate_baseline(value["baseline"], tasks)
    )
    return {
        "aggregate_only": True,
        "baseline": baseline,
        "buckets": buckets,
        "coverage": "native_schema_3",
        "schema_version": STORE_SCHEMA_VERSION,
        "weights": weights,
    }


def _empty_store() -> dict[str, object]:
    return {
        "aggregate_only": True,
        "baseline": {"counted_tasks": 0, "relative_cost_micros_total": 0, "tasks": 0},
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
        return _validate_store(
            json.loads(
                payload.decode("ascii"),
                object_pairs_hook=json_object_pairs_without_duplicates,
            )
        )
    except (UnicodeDecodeError, ValueError, RecursionError, UsageAggregationError):
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


def _weight_micros(
    weights: list[dict[str, object]],
    agent: str,
    model: str | None,
    effort: str,
) -> int | None:
    """Return the configured relative cost for one agent/model at one effort."""
    key = (agent, model or "", effort)
    return next(
        (_counter(item["relative_cost_micros"]) for item in weights if _weight_key(item) == key),
        None,
    )


def _baseline_micros(
    weights: list[dict[str, object]],
    source_agent: str,
) -> int | None:
    """Return what one task would have cost on the fixed route it skipped.

    모델은 일부러 빼고 조회한다. 반사실은 "라우팅하지 않았다면" 이고, 라우팅하지
    않았을 때 가는 내장 standard 라우트는 모델을 고정하지 않는다(벤더 기본 모델).
    라우팅된 모델을 그대로 기준선에 쓰면 모델 라우팅이 개입한 바로 그 경우에
    존재한 적 없는 반사실의 가격을 매기게 된다. 싼 모델로 보냈다면 기준선까지
    같이 싸져 절감이 사라지고, 비싼 모델로 보냈다면 초과 지출이 가려진다.

    에이전트는 라우팅 전 소스 벤더를 쓴다. 기본 라우팅에서는 실행 에이전트와
    같고, 명시적으로 허용한 크로스 벤더 라우팅에서는 실행 에이전트와 다르다.
    """
    return _weight_micros(weights, source_agent, None, BASELINE_EFFORT)


def record_usage(
    path: Path,
    dimensions: UsageDimensions,
    *,
    child_returncode: int,
    rework: bool,
    escalation: bool,
    source_vendor: str | None = None,
) -> None:
    """Record one finished child.

    ``rework`` 는 이 실행이 이미 센 태스크의 재시도라는 뜻이다. 참이면 실행 수만
    늘고 태스크 수와 기준선은 늘지 않는다. 그래서 저비용 라우팅이 실패해 다시
    도는 비용이 기준선에 흡수되지 않고 초과 지출로 남는다.

    ``source_vendor`` 는 라우팅하지 않았을 때의 기준선 조회에만 쓰며 저장하지
    않는다. 생략하면 실행 에이전트와 같은 벤더에서 시작한 것으로 본다.
    """
    if not isinstance(rework, bool) or not isinstance(escalation, bool):
        raise UsageAggregationError()
    normalized: dict[str, object] = {
        "agent": _agent(dimensions.agent),
        "model": _model(dimensions.model),
        "effort": _effort(dimensions.effort),
        "tier": _tier(dimensions.tier),
    }
    baseline_agent = _agent(dimensions.agent if source_vendor is None else source_vendor)
    status = _status_key(child_returncode)

    def update(value: dict[str, object]) -> None:
        weights = value["weights"]
        buckets = value["buckets"]
        baseline = value["baseline"]
        assert isinstance(weights, list)
        assert isinstance(buckets, list)
        assert isinstance(baseline, dict)
        weight = _weight_micros(
            weights,
            str(normalized["agent"]),
            normalized["model"] if isinstance(normalized["model"], str) else None,
            str(normalized["effort"]),
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
        if rework:
            return
        # 첫 시도만 태스크로 센다. 이 태스크가 라우팅 없이 갔을 고정 경로의 비용을
        # 그 자리에서 확정해 둔다. 나중에 가중치가 바뀌어도 이미 쌓인 기준선은
        # 다시 쓰이지 않는다. 실제 비용과 같은 규칙이다.
        baseline["tasks"] = _increment(int(baseline["tasks"]))
        baseline_weight = _baseline_micros(weights, baseline_agent)
        if baseline_weight is not None:
            baseline["counted_tasks"] = _increment(int(baseline["counted_tasks"]))
            baseline["relative_cost_micros_total"] = _increment(
                int(baseline["relative_cost_micros_total"]), baseline_weight
            )

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
    baseline = value["baseline"]
    assert isinstance(baseline, dict)
    return {
        "aggregate_only": True,
        "buckets": [_render_metrics(bucket) | _dimensions(bucket) for bucket in buckets],
        "claims": {
            "baseline_is_counterfactual": True,
            "first_attempts_self_reported": True,
            "pricing_verified": False,
            "relative_cost_only": True,
            "task_content_recorded": False,
            "weights_apply_prospectively": True,
        },
        "coverage": "native_schema_3",
        "schema_version": STORE_SCHEMA_VERSION,
        "totals": _render_metrics(totals) | _render_savings(totals, baseline),
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


def _savings_reason_code(
    tasks: int,
    counted_tasks: int,
    unweighted_runs: int,
    baseline_micros: int,
) -> str:
    """Return why a savings ratio is or is not computable.

    부분 증거로 절감을 계산하지 않는다. 가중치가 없는 실행이 하나라도 있으면 실제
    비용이 과소 집계되고, 기준선 가중치가 없는 태스크가 있으면 반사실이 과소
    집계된다. 어느 쪽이든 결과는 절감을 실제보다 좋아 보이게 만든다.
    """
    if tasks == 0:
        return "no_tasks"
    if unweighted_runs:
        return "unweighted_runs"
    if counted_tasks != tasks or baseline_micros == 0:
        return "missing_baseline_weight"
    return "computed"


def _render_savings(
    totals: Mapping[str, object],
    baseline: Mapping[str, object],
) -> dict[str, object]:
    """Compare measured cost against the fixed route the tasks would have taken."""
    tasks = _counter(baseline["tasks"])
    counted_tasks = _counter(baseline["counted_tasks"])
    baseline_micros = _counter(baseline["relative_cost_micros_total"])
    measured_micros = _counter(totals["relative_cost_micros_total"])
    reason_code = _savings_reason_code(
        tasks,
        counted_tasks,
        _counter(totals["unweighted_runs"]),
        baseline_micros,
    )
    computed = reason_code == "computed"
    savings_micros = baseline_micros - measured_micros
    return {
        "baseline_effort": BASELINE_EFFORT,
        "baseline_tasks": counted_tasks,
        "relative_cost_baseline_units": (
            f"{Decimal(baseline_micros) / Decimal(WEIGHT_SCALE):.6f}" if computed else None
        ),
        "relative_cost_savings_ratio": (
            f"{Decimal(savings_micros) / Decimal(baseline_micros):.6f}" if computed else None
        ),
        "relative_cost_savings_units": (
            f"{Decimal(savings_micros) / Decimal(WEIGHT_SCALE):.6f}" if computed else None
        ),
        "savings_reason_code": reason_code,
    }


def _render_metrics(value: Mapping[str, object]) -> dict[str, object]:
    runs = _counter(value["runs"])
    weighted_runs = _counter(value["weighted_runs"])
    relative_cost_micros = _counter(value["relative_cost_micros_total"])
    reworks = _counter(value["reworks"])
    return {
        "escalation_ratio": _ratio(_counter(value["escalations"]), runs),
        "escalations": value["escalations"],
        "failed": value["failed"],
        "lower_weight_ratio": _ratio(_counter(value["lower_weight_runs"]), weighted_runs),
        "lower_weight_runs": value["lower_weight_runs"],
        "relative_cost_units": _cost_units(relative_cost_micros, weighted_runs),
        "rework_ratio": _ratio(reworks, runs),
        "reworks": reworks,
        "runs": runs,
        "runs_per_task": _ratio(runs, runs - reworks),
        "status_counts": value["status_counts"],
        "succeeded": value["succeeded"],
        "tasks": runs - reworks,
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
