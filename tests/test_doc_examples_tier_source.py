"""문서의 `route`/`run` 예제는 티어 출처를 반드시 실어야 한다.

`route` 와 `run` 이 `--tier` 또는 `--suggest-tier` 중 정확히 하나를 요구하게
바뀐 뒤에도 `docs/integrations.md` 의 예제는 둘 다 없이 남아 있었다. 그대로
따라 하면 `invalid_input` 으로 끝나므로, 저장소의 모든 마크다운 문서에서
fenced code block 안의 `wclass route`/`run` 호출을 읽어 현재 계약을 만족하는지
검사한다. 산문 속 언급은 호출이 아니므로 펜스 밖은 보지 않는다.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 저장소 밖 도구가 만드는 디렉터리와 의존성 사본은 공개 문서가 아니다.
EXCLUDED_PARTS = frozenset({".git", ".omc", ".venv", "node_modules", "build", "dist"})
# 실행 예제는 셸 블록에만 산다. `markdown` 이나 `text` 블록은 산문이나 다른
# 문서를 인용하며, 그 안의 언급은 호출이 아니다.
FENCE_OPEN = re.compile(r"^\s*```(sh|bash|shell|console|zsh)\s*$")
FENCE_CLOSE = re.compile(r"^\s*```\s*$")
INVOCATION = re.compile(r"\bwclass (?:route|run)\b[^\n]*")
TIER_SOURCE = re.compile(r"(?:^|\s)--(?:tier|suggest-tier)(?![\w-])")


def _public_documents() -> list[Path]:
    """Return every tracked-looking markdown file outside tool and dependency directories."""
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not (EXCLUDED_PARTS & set(path.relative_to(ROOT).parts))
    )


def _fenced_blocks(text: str) -> list[str]:
    """Return the body of each shell code block, in document order."""
    blocks: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if current is None:
            if FENCE_OPEN.match(line):
                current = []
            continue
        if FENCE_CLOSE.match(line):
            blocks.append("\n".join(current))
            current = None
            continue
        current.append(line)
    return blocks


def _invocations(block: str) -> list[str]:
    """Return every `wclass route`/`run` call in one block, continuation lines joined."""
    joined = re.sub(r"\\\n\s*", " ", block)
    return [match.group(0) for match in INVOCATION.finditer(joined)]


class DocumentedRouteExamplesTests(unittest.TestCase):
    def test_the_scan_sees_the_documents_it_exists_for(self) -> None:
        """Breaks if the document walk silently stops covering the known example files."""
        names = {path.relative_to(ROOT).as_posix() for path in _public_documents()}
        self.assertLessEqual({"README.md", "docs/integrations.md"}, names)

    def test_the_scan_is_not_vacuous(self) -> None:
        """Breaks if the fence parser or the regex stop finding a real documented call."""
        block = "```sh\nprintf '%s' 'x' | \\\n  wclass run --source-vendor codex\n```\n"
        self.assertEqual(
            [call for fenced in _fenced_blocks(block) for call in _invocations(fenced)],
            ["wclass run --source-vendor codex"],
        )
        self.assertIsNone(TIER_SOURCE.search("wclass run --source-vendor codex"))
        self.assertIsNotNone(TIER_SOURCE.search("wclass route --policy p.json --suggest-tier)"))
        self.assertEqual(_fenced_blocks("```markdown\nwclass run\n```\n"), [])

    def test_every_documented_route_or_run_example_names_its_tier_source(self) -> None:
        """Breaks if a public document shows a `route`/`run` call the CLI would reject."""
        for document in _public_documents():
            text = document.read_text(encoding="utf-8")
            for block in _fenced_blocks(text):
                for invocation in _invocations(block):
                    with self.subTest(document=str(document), invocation=invocation):
                        self.assertRegex(invocation, TIER_SOURCE)


if __name__ == "__main__":
    unittest.main()
