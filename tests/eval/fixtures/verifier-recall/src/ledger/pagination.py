"""페이지 분할. 마지막 페이지의 마지막 항목까지 포함한다."""

from collections.abc import Sequence


def page(entries: Sequence[str], page_size: int, index: int) -> tuple[str, ...]:
    """0 기반 `index` 번째 페이지를 돌려준다.

    `page_size` 는 1 이상, `index` 는 0 이상이어야 한다. 범위를 벗어난 페이지는
    빈 튜플이다. 경계는 `start:start + page_size` 로 잘라 마지막 항목이 빠지지
    않게 한다.
    """
    if page_size < 1 or index < 0:
        raise ValueError("page_size must be >= 1 and index >= 0")
    start = index * page_size
    return tuple(entries[start : start + page_size])
