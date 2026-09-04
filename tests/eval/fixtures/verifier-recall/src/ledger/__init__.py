"""검증기 재현율 하네스의 합성 픽스처.

실제 서비스가 아니라 측정 대상이다. `docs/cheap-first-decision-plan.md` A0 가
문서화한 네 가지 결함 클래스(스키마 강제, 손상 상태 무음 덮어쓰기, 식별자
패딩, 가변 내부 상태 반환)가 자연스럽게 생길 수 있는 최소한의 "원장" 모듈로,
각 동작은 저장소가 기록한 결함의 올바른 형태를 구현한다.
"""

from ledger.cache import Ledger
from ledger.ids import LedgerIdError, normalize_ledger_id
from ledger.pagination import page
from ledger.schema import SCHEMA_VERSION, SchemaError, validate_schema_version
from ledger.state import StateCorruptError, load_state, save_state

__all__ = [
    "SCHEMA_VERSION",
    "Ledger",
    "LedgerIdError",
    "SchemaError",
    "StateCorruptError",
    "load_state",
    "normalize_ledger_id",
    "page",
    "save_state",
    "validate_schema_version",
]
