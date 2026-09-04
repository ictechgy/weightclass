"""스키마 버전 검사. 정확히 정수 1 만 받는다."""

SCHEMA_VERSION = 1


class SchemaError(ValueError):
    """지원하지 않거나 형식이 틀린 스키마 버전."""


def validate_schema_version(value: object) -> int:
    """스키마 버전을 검증해 정수로 돌려준다.

    `bool` 은 `int` 의 하위 타입이라 `True == 1` 이 참이다. 그래서 `==` 비교나
    `isinstance(value, int)` 로는 `True` 를 거르지 못한다. 타입을 정확히 본다.
    """
    if type(value) is not int or value != SCHEMA_VERSION:
        raise SchemaError("schema_version must be the integer 1")
    return value
