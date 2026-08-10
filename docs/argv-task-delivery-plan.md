# argv Task Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a route carry the task in its argv so `agy` and `grok` become usable native vendors, without putting task content into review output or route fingerprints.

**Architecture:** A reserved argv token `{{task}}` marks where the task goes. Policy parsing enforces at most one token used as a whole element. Review and fingerprinting run on the unsubstituted command, so both stay task-free and one review still binds many runs. Substitution happens once, immediately before spawn, and that child gets empty stdin instead of the task.

**Tech Stack:** Python 3.10+, standard library only, `unittest`, `ruff`, `mypy --strict`.

**Design:** `docs/argv-task-delivery-design.md` (approved).

## Global Constraints

- No runtime dependencies. `pyproject.toml` declares `dependencies = []`; keep it empty.
- Python floor is 3.10. Do not use syntax newer than 3.10.
- Code comments in Korean. Documentation prose in English, matching existing `docs/`.
- Diagnostics never contain caller-supplied values. Errors are the fixed set already in `cli.py`: `invalid_input`, `invalid_task`, `unsupported_route`, `executor_unavailable`, `route_fingerprint_mismatch`, `executor_failed`, `triage_unavailable`.
- Task content is never logged, persisted, hashed, or placed in review output or diagnostics.
- Tests never invoke a real vendor CLI. Use fake executables, as existing native tests do.
- `ruff check`, `ruff format --check`, and `mypy --strict src/weightclass tests` must pass at every commit.
- Run the suite with `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -t . -q`.

---

### Task 1: Reserve the `{{task}}` token and reject malformed use

**Files:**
- Modify: `src/weightclass/router.py` (add constant and two helpers near `codex_command`)
- Modify: `src/weightclass/cli.py:195-224` (`_parse_route`)
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `router.TASK_PLACEHOLDER: Final = "{{task}}"`
  - `router.uses_argv_task_delivery(command: tuple[str, ...]) -> bool`
  - `router.substitute_task(command: tuple[str, ...], task: str) -> tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_router.py`, after the `PolicyRunBindingTests` class:

```python
class TaskPlaceholderTests(unittest.TestCase):
    """{{task}} 는 명령 안에서 태스크가 들어갈 자리를 표시한다.

    stdin 을 읽지 않고 프롬프트를 인자로만 받는 CLI 가 있기 때문이다. 자리를
    잘못 쓴 정책은 파싱 단계에서 닫는다. 실행 직전에 발견하면 이미 늦다.
    """

    def _policy(self, directory: Path, command: list[str]) -> Path:
        policy_path = directory / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "routes": [
                        {"id": "r", "vendor": "codex", "tier": "low", "command": command}
                    ]
                }
            ),
            encoding="utf-8",
        )
        return policy_path

    def test_one_whole_token_is_accepted(self) -> None:
        """Breaks if the reserved slot cannot be declared at all."""
        with tempfile.TemporaryDirectory() as directory:
            path = self._policy(Path(directory), ["/bin/echo", "{{task}}"])
            routes = cli.load_routes(path)

        self.assertEqual(routes[0].command, ("/bin/echo", "{{task}}"))
        self.assertTrue(router.uses_argv_task_delivery(routes[0].command))

    def test_no_token_means_stdin_delivery(self) -> None:
        """Breaks if existing policies silently change delivery mode."""
        with tempfile.TemporaryDirectory() as directory:
            path = self._policy(Path(directory), ["/bin/echo", "ok"])
            routes = cli.load_routes(path)

        self.assertFalse(router.uses_argv_task_delivery(routes[0].command))

    def test_two_tokens_are_rejected(self) -> None:
        """Breaks if a task could be delivered twice with no defined meaning."""
        with tempfile.TemporaryDirectory() as directory:
            path = self._policy(Path(directory), ["/bin/echo", "{{task}}", "{{task}}"])
            with self.assertRaises(cli.InvalidInputError):
                cli.load_routes(path)

    def test_the_token_inside_a_larger_argument_is_rejected(self) -> None:
        """Breaks if how the task and a flag were joined becomes ambiguous."""
        for argument in ("--prompt={{task}}", "prefix{{task}}", "{{task}}suffix"):
            with self.subTest(argument=argument), tempfile.TemporaryDirectory() as directory:
                path = self._policy(Path(directory), ["/bin/echo", argument])
                with self.assertRaises(cli.InvalidInputError):
                    cli.load_routes(path)

    def test_substitution_fills_exactly_the_reserved_slot(self) -> None:
        """Breaks if substitution touches an argument it was not given."""
        filled = router.substitute_task(("agy", "--print", "{{task}}", "--effort"), "긴 태스크")

        self.assertEqual(filled, ("agy", "--print", "긴 태스크", "--effort"))
```

Add `router` to the module imports at the top of `tests/test_router.py`:

```python
from weightclass import cli, router
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_router.TaskPlaceholderTests -v`
Expected: FAIL with `AttributeError: module 'weightclass.router' has no attribute 'TASK_PLACEHOLDER'` or `uses_argv_task_delivery`.

- [ ] **Step 3: Add the constant and helpers**

In `src/weightclass/router.py`, immediately after `codex_command`:

```python
# 프롬프트를 stdin 이 아니라 인자로만 받는 CLI 가 있다. 그런 명령에서 태스크가
# 들어갈 자리를 이 토큰으로 표시한다. 이 토큰이 없으면 지금까지처럼 stdin 으로
# 전달한다.
TASK_PLACEHOLDER: Final = "{{task}}"


def uses_argv_task_delivery(command: tuple[str, ...]) -> bool:
    """Report whether this command carries the task in argv rather than on stdin."""
    return TASK_PLACEHOLDER in command


def substitute_task(command: tuple[str, ...], task: str) -> tuple[str, ...]:
    """Fill the single reserved slot. Call this only immediately before spawn.

    치환된 argv 는 실행에만 쓴다. 검토 출력과 지문은 치환 전 명령으로 계산해야
    태스크가 그 둘에 새어 들어가지 않는다.
    """
    return tuple(task if token == TASK_PLACEHOLDER else token for token in command)
```

- [ ] **Step 4: Enforce the token rules when a policy is parsed**

In `src/weightclass/cli.py`, add this helper immediately before `_parse_route`:

```python
def _require_at_most_one_task_slot(command: tuple[str, ...]) -> tuple[str, ...]:
    """Require the reserved task token to appear at most once, as a whole argument.

    부분 문자열로 쓰면 태스크와 플래그가 어떻게 이어붙었는지가 모호해지고, 두 번
    쓰면 태스크를 두 번 전달한다는 뜻이 되는데 그런 의미는 정의된 적이 없다.
    """
    if sum(token == TASK_PLACEHOLDER for token in command) > 1:
        raise InvalidInputError()
    if any(TASK_PLACEHOLDER in token and token != TASK_PLACEHOLDER for token in command):
        raise InvalidInputError()
    return command
```

Replace the `Route(...)` construction at the end of `_parse_route` so the command is validated before the route is built:

```python
    parsed_command = _require_at_most_one_task_slot(
        tuple(_require_command_argument(argument) for argument in command)
    )
    return Route(
        route_id=route_id,
        vendor=vendor,
        workflow=workflow,
        command=parsed_command,
        tier=tier,
    )
```

Add `TASK_PLACEHOLDER` to the existing `from .router import (...)` block in `cli.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_router -q`
Expected: OK.

- [ ] **Step 6: Run the full suite and the gates**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -t . -q`
Expected: OK, count is 633 + 5 new = 638.

Run: `ruff check . && ruff format --check . && mypy --strict src/weightclass tests`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/weightclass/router.py src/weightclass/cli.py tests/test_router.py
git commit -m "feat: 명령 안에 태스크 자리를 표시하는 {{task}} 토큰 도입

프롬프트를 stdin 이 아니라 인자로만 받는 CLI 를 붙이려면 명령 안에 태스크가
들어갈 자리가 필요하다. 토큰을 예약하고, 두 번 쓰거나 부분 문자열로 쓴 정책은
파싱 단계에서 닫는다. 아직 치환은 하지 않는다."
```

---

### Task 2: Substitute at spawn and send that child empty stdin

**Files:**
- Modify: `src/weightclass/cli.py:967-986` (`run_from_standard_input`)
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: `router.uses_argv_task_delivery`, `router.substitute_task`, `router.TASK_PLACEHOLDER` from Task 1.
- Produces: no new symbols. `wclass run` delivers the task through argv when the selected route declares the slot.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_router.py`, inside `TaskPlaceholderTests`:

```python
    def _recorder(self, directory: Path) -> Path:
        """자식이 받은 argv 와 stdin 을 그대로 파일에 적는 가짜 실행 파일."""
        recorder = directory / "recorder.py"
        recorder.write_text(
            "import json, sys\n"
            "record = {'argv': sys.argv[1:], 'stdin': sys.stdin.read()}\n"
            "open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(record))\n",
            encoding="utf-8",
        )
        return recorder

    def test_argv_delivery_puts_the_task_in_argv_and_leaves_stdin_empty(self) -> None:
        """Breaks if the task is delivered twice or in the wrong channel."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = self._recorder(root)
            record_path = root / "record.json"
            policy_path = self._policy(
                root,
                [sys.executable, str(recorder), str(record_path), "{{task}}"],
            )

            result = reviewed_run(policy_path, "Fix a typo.")
            recorded = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(recorded["argv"][-1], "Fix a typo.")
        self.assertEqual(recorded["stdin"], "")

    def test_stdin_delivery_is_unchanged(self) -> None:
        """Breaks if adding argv delivery altered the path every existing policy uses."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorder = self._recorder(root)
            record_path = root / "record.json"
            policy_path = self._policy(root, [sys.executable, str(recorder), str(record_path)])

            result = reviewed_run(policy_path, "Fix a typo.")
            recorded = json.loads(record_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(recorded["stdin"], "Fix a typo.")
        self.assertNotIn("Fix a typo.", recorded["argv"])

    def test_a_task_carrying_nul_is_refused_before_spawn(self) -> None:
        """Breaks if an argv-delivery run reaches execve with a byte it cannot carry."""
        with tempfile.TemporaryDirectory() as directory:
            policy_path = self._policy(Path(directory), ["/bin/echo", "{{task}}"])
            result = reviewed_run(policy_path, "before\x00after")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {"error": "invalid_task"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_router.TaskPlaceholderTests -v`
Expected: FAIL. `test_argv_delivery_...` fails because the recorder receives `{{task}}` literally and reads the task from stdin.

- [ ] **Step 3: Substitute immediately before spawn**

In `src/weightclass/cli.py`, replace the `subprocess.run` call inside `run_from_standard_input` (currently at lines 980-986) with:

```python
        # 치환은 spawn 직전에 한 번만 한다. 검토 출력과 지문은 이미 치환 전
        # 명령으로 계산되었으므로 태스크가 그 둘에 들어가지 않는다.
        argv = route.command
        child_input = task.encode("utf-8")
        if uses_argv_task_delivery(route.command):
            # execve 는 NUL 을 실을 수 없다. stdin 전달은 실을 수 있으므로 이
            # 거부는 argv 전달에만 적용한다.
            if "\x00" in task:
                raise InvalidTaskError()
            argv = substitute_task(route.command, task)
            child_input = b""
        # text 모드는 로케일 인코딩을 사용하므로 LC_ALL=C 환경에서 비ASCII 태스크가
        # UnicodeEncodeError로 새어 나간다. 자식 출력을 읽지 않으므로 바이트로 전달한다.
        completed_process = subprocess.run(
            argv,
            check=False,
            input=child_input,
        )
```

Add `substitute_task` and `uses_argv_task_delivery` to the existing `from .router import (...)` block in `cli.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_router -q`
Expected: OK.

- [ ] **Step 5: Run the full suite and the gates**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -t . -q`
Expected: OK, 641 tests.

Run: `ruff check . && ruff format --check . && mypy --strict src/weightclass tests`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/weightclass/cli.py tests/test_router.py
git commit -m "feat: argv 전달 라우트의 태스크를 spawn 직전에 치환

{{task}} 를 선언한 라우트는 태스크를 argv 로 받고 stdin 은 비운다. 두 경로로
동시에 전달하면 자식이 태스크를 두 번 보게 된다. execve 가 실을 수 없는 NUL 은
argv 전달에서만 거부한다. stdin 전달은 실을 수 있으므로 그대로 둔다."
```

---

### Task 3: Surface argv delivery in the review descriptor

**Files:**
- Modify: `src/weightclass/cli.py:900-925` (`route_from_standard_input` response)
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: `router.uses_argv_task_delivery` from Task 1.
- Produces: `wclass route` emits `"task_delivery": "argv"` for argv-delivery routes and omits the key otherwise.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_router.py`, inside `TaskPlaceholderTests`:

```python
    def test_review_names_argv_delivery_and_never_shows_the_task(self) -> None:
        """Breaks if a reviewer cannot see that this route puts the task on the command line."""
        with tempfile.TemporaryDirectory() as directory:
            policy_path = self._policy(Path(directory), ["/bin/echo", "{{task}}"])
            review = _weightclass(
                "route", "--policy", str(policy_path), task="비밀 태스크"
            )

        rendered = json.loads(review.stdout)
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(rendered["task_delivery"], "argv")
        self.assertEqual(rendered["command"], ["/bin/echo", "{{task}}"])
        self.assertNotIn("비밀 태스크", review.stdout)

    def test_review_omits_the_key_for_stdin_delivery(self) -> None:
        """Breaks if every existing review output grows a field it never had."""
        with tempfile.TemporaryDirectory() as directory:
            policy_path = self._policy(Path(directory), ["/bin/echo", "ok"])
            review = _weightclass("route", "--policy", str(policy_path), task="Fix a typo.")

        self.assertNotIn("task_delivery", json.loads(review.stdout))

    def test_two_tasks_at_one_tier_share_one_fingerprint(self) -> None:
        """Breaks if the fingerprint starts covering substituted task text.

        지문이 태스크마다 달라지면 한 번의 검토가 한 번의 실행만 묶게 되고,
        태스크의 해시를 남기지 않는다는 규칙도 사실상 깨진다.
        """
        with tempfile.TemporaryDirectory() as directory:
            policy_path = self._policy(Path(directory), ["/bin/echo", "{{task}}"])
            first = _weightclass("route", "--policy", str(policy_path), task="Fix a typo.")
            second = _weightclass("route", "--policy", str(policy_path), task="Rename a var.")

        self.assertEqual(
            json.loads(first.stdout)["route_fingerprint"],
            json.loads(second.stdout)["route_fingerprint"],
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_router.TaskPlaceholderTests -v`
Expected: FAIL with `KeyError: 'task_delivery'`.

- [ ] **Step 3: Add the field**

In `src/weightclass/cli.py`, inside `route_from_standard_input`, after the block that adds `posture` and `reason_code`:

```python
    # argv 전달은 태스크를 명령줄에 싣는다. 같은 머신의 다른 사용자가 ps 로 볼 수
    # 있으므로, 검토하는 사람이 이 사실을 모르고 지나치지 않게 명시한다.
    if uses_argv_task_delivery(route.command):
        response["task_delivery"] = "argv"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_router -q`
Expected: OK.

- [ ] **Step 5: Run the full suite and the gates**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -t . -q`
Expected: OK, 644 tests.

Run: `ruff check . && ruff format --check . && mypy --strict src/weightclass tests`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/weightclass/cli.py tests/test_router.py
git commit -m "feat: 검토 출력에 argv 전달 여부를 표시

argv 전달 라우트는 태스크를 명령줄에 싣고, 명령줄은 같은 머신의 다른 사용자에게
보인다. 검토하는 사람이 이걸 모르고 지나치면 안 되므로 route 출력에 명시한다.
stdin 전달 라우트의 출력은 그대로 두어 기존 소비자를 깨지 않는다."
```

---

### Task 4: Give the V2 API path its own vendor set

**Files:**
- Modify: `src/weightclass/v2.py:19` and `src/weightclass/v2.py:151-169` (`select_api_route`)
- Test: `tests/test_v2.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `v2.API_SOURCE_VENDORS: Final[frozenset[str]]`, the vendors whose provider and billing boundary are known.

This task must land before Task 5. `select_api_route` currently admits anything in `SUPPORTED_VENDORS` and then indexes `SOURCE_PROVIDER`, so the first vendor added without a provider entry turns a diagnostic into a `KeyError` traceback.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_v2.py`, at the end of the file before the `if __name__` block:

```python
class ApiVendorScopeTests(unittest.TestCase):
    def test_a_vendor_without_a_known_provider_is_an_unsupported_route(self) -> None:
        """Breaks if the API path admits a vendor it cannot place on a billing boundary.

        교차-provider 차단은 벤더에서 provider 를 유도해 성립한다. provider 를
        모르는 벤더는 그 차단을 통과시킬 근거가 없으므로 닫는다.
        """
        policy = ApiRoutingPolicy(
            routes=(
                ApiRoute(
                    route_id="r",
                    tier="low",
                    eligible_source_vendors=("codex", "agy"),
                    provider="openai",
                    transport="api",
                    model="m",
                    effort="low",
                    intended_recipient="OpenAI API",
                    intended_billing_boundary="user OpenAI API account",
                ),
            ),
            allow_cross_provider=False,
            allow_api=True,
        )

        with self.assertRaises(RouteSelectionError):
            select_api_route("Fix a typo.", policy, "agy")
```

Ensure `tests/test_v2.py` imports `ApiRoute`, `ApiRoutingPolicy`, `select_api_route`, and `RouteSelectionError`. Check the existing import block first and add only what is missing:

```python
from weightclass.router import RouteSelectionError
from weightclass.v2 import ApiRoute, ApiRoutingPolicy, select_api_route
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_v2.ApiVendorScopeTests -v`
Expected: FAIL with `KeyError: 'agy'` raised from `SOURCE_PROVIDER[source_vendor]`.

- [ ] **Step 3: Introduce the API vendor set**

In `src/weightclass/v2.py`, immediately after `SOURCE_PROVIDER`:

```python
# API 경로는 벤더에서 provider 를 유도해 교차-provider 를 차단한다. 그 매핑이 없는
# 벤더는 어디로 과금되는지 판단할 근거가 없으므로 이 경로에서 제외한다. 네이티브
# 경로의 SUPPORTED_VENDORS 와 일부러 분리한다.
API_SOURCE_VENDORS: Final = frozenset(SOURCE_PROVIDER)
```

In `select_api_route`, replace the guard:

```python
    if source_vendor not in API_SOURCE_VENDORS or not policy.allow_api:
        raise RouteSelectionError()
```

Remove the now-unused `SUPPORTED_VENDORS` import from `v2.py` only if nothing else in the file uses it. Check with `grep -n SUPPORTED_VENDORS src/weightclass/v2.py` before editing the import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_v2 -q`
Expected: OK.

- [ ] **Step 5: Run the full suite and the gates**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -t . -q`
Expected: OK, 645 tests.

Run: `ruff check . && ruff format --check . && mypy --strict src/weightclass tests`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/weightclass/v2.py tests/test_v2.py
git commit -m "refactor: V2 API 경로의 벤더 집합을 네이티브와 분리

API 경로는 벤더에서 provider 를 유도해 교차-provider 를 차단한다. 지금은
SUPPORTED_VENDORS 를 통과시킨 뒤 SOURCE_PROVIDER 를 인덱싱하므로, provider 매핑이
없는 벤더가 추가되는 순간 진단 대신 KeyError 트레이스백이 난다. 두 집합을 분리해
그 벤더를 unsupported_route 로 닫는다."
```

---

### Task 5: Add `agy` as a built-in vendor

**Files:**
- Modify: `src/weightclass/router.py` (`SUPPORTED_VENDORS`, new prefix and builder, `DEFAULT_ROUTES`)
- Modify: `src/weightclass/triage.py:90` (`TRIAGE_UNAVAILABLE_REASONS`)
- Test: `tests/test_router.py`, `tests/test_triage.py`

**Interfaces:**
- Consumes: `router.TASK_PLACEHOLDER` from Task 1; the substitution path from Task 2; `v2.API_SOURCE_VENDORS` from Task 4.
- Produces: `router.agy_command(reasoning_effort: str) -> tuple[str, ...]`, and `"agy"` in `router.SUPPORTED_VENDORS`.

Verified against the installed build on 2026-08-10: `agy --print <PROMPT>` runs one non-interactive prompt, `--effort` accepts `low|medium|high`, and `--mode accept-edits` auto-approves edits. `agy --print ""` fails with `Error: empty prompt`, which is why the token is a separate argv element rather than an empty one.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_router.py`, after `TaskPlaceholderTests`:

```python
class AgyBuiltInRouteTests(unittest.TestCase):
    def test_every_tier_has_an_agy_route_that_carries_the_task_in_argv(self) -> None:
        """Breaks if a tier is missing or the built-in stops declaring its task slot."""
        for tier, effort in (("low", "low"), ("standard", "medium"), ("high", "high")):
            with self.subTest(tier=tier):
                route = select_tier_route(DEFAULT_ROUTES, tier, "agy")

                self.assertEqual(route.vendor, "agy")
                self.assertEqual(route.command[0], "agy")
                self.assertIn(router.TASK_PLACEHOLDER, route.command)
                self.assertEqual(route.command[route.command.index("--effort") + 1], effort)
                self.assertIn("--mode", route.command)
                self.assertEqual(route.command[route.command.index("--mode") + 1], "accept-edits")

    def test_agy_is_a_supported_source_vendor(self) -> None:
        """Breaks if the vendor label is rejected by the surfaces that gate routing."""
        self.assertIn("agy", SUPPORTED_VENDORS)

    def test_the_default_vendor_is_still_codex(self) -> None:
        """Breaks if adding a vendor changed which route an unqualified call selects."""
        route = select_tier_route(DEFAULT_ROUTES, "low")

        self.assertEqual(route.vendor, "codex")
```

Extend the `weightclass.router` import in `tests/test_router.py` to include `SUPPORTED_VENDORS` and `select_tier_route`.

Add to `tests/test_triage.py`, inside `TriageCommandTests`:

```python
    def test_agy_has_no_reviewed_triage_adapter(self) -> None:
        """Breaks if an unreviewed adapter starts sending task text to a new vendor."""
        with self.assertRaises(TriageUnavailableError):
            triage_command("agy")

        descriptor = triage_descriptor("agy")
        self.assertFalse(descriptor["available"])
        self.assertEqual(descriptor["unavailable_reason"], "no_reviewed_triage_adapter")
        self.assertNotIn("command", descriptor)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_router.AgyBuiltInRouteTests tests.test_triage.TriageCommandTests -v`
Expected: FAIL with `RouteSelectionError` for the agy tiers, and `KeyError`/assertion failure for the triage descriptor.

- [ ] **Step 3: Add the vendor, its command builder, and its routes**

In `src/weightclass/router.py`, extend the vendor set:

```python
SUPPORTED_VENDORS: Final = frozenset({"claude", "codex", "agy"})
```

Add after `codex_command` and the placeholder helpers:

```python
# agy 는 프롬프트를 stdin 에서 읽지 않는다. --print 가 프롬프트를 인자로 요구하고,
# 빈 문자열을 주면 empty prompt 오류로 닫힌다. 그래서 태스크 자리를 argv 에 둔다.
# --effort 어휘가 이 저장소의 티어 어휘와 같아 그대로 대응된다.
AGY_COMMAND_PREFIX: Final = (
    "agy",
    "--print",
    TASK_PLACEHOLDER,
    "--mode",
    "accept-edits",
    "--effort",
)


def agy_command(reasoning_effort: str) -> tuple[str, ...]:
    """Build the built-in Antigravity command for one reasoning effort label."""
    return AGY_COMMAND_PREFIX + (reasoning_effort,)
```

Append to `DEFAULT_ROUTES`, after the existing claude entries:

```python
    Route(
        route_id="agy-low",
        vendor="agy",
        workflow="",
        tier="low",
        command=agy_command("low"),
    ),
    Route(
        route_id="agy-standard",
        vendor="agy",
        workflow="",
        tier="standard",
        command=agy_command("medium"),
    ),
    Route(
        route_id="agy-high",
        vendor="agy",
        workflow="",
        tier="high",
        command=agy_command("high"),
    ),
```

In `src/weightclass/triage.py`, extend the reasons table:

```python
TRIAGE_UNAVAILABLE_REASONS: Final = {
    "codex": "no_no_tools_boundary",
    "agy": "no_reviewed_triage_adapter",
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_router tests.test_triage -q`
Expected: OK.

- [ ] **Step 5: Run the full suite and the gates**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -t . -q`
Expected: OK, 649 tests.

Run: `ruff check . && ruff format --check . && mypy --strict src/weightclass tests`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/weightclass/router.py src/weightclass/triage.py tests/test_router.py tests/test_triage.py
git commit -m "feat: 내장 벤더로 agy 추가

설치된 빌드로 확인했다. agy --print 는 프롬프트를 인자로 요구하고 빈 문자열은
empty prompt 오류로 닫히므로, 태스크 자리를 argv 에 둔다. --effort 어휘가 이
저장소의 티어 어휘와 같아 그대로 대응된다.

판정 어댑터는 넣지 않는다. 검토되지 않은 어댑터로 신뢰할 수 없는 태스크를 새
도구에 보내는 것은 어댑터가 없는 것보다 나쁘다."
```

---

### Task 6: Add `grok` as a built-in vendor

**Files:**
- Modify: `src/weightclass/router.py` (`SUPPORTED_VENDORS`, new prefix and builder, `DEFAULT_ROUTES`)
- Modify: `src/weightclass/triage.py` (`TRIAGE_UNAVAILABLE_REASONS`)
- Test: `tests/test_router.py`, `tests/test_triage.py`

**Interfaces:**
- Consumes: `router.TASK_PLACEHOLDER` from Task 1; the substitution path from Task 2; `v2.API_SOURCE_VENDORS` from Task 4.
- Produces: `router.grok_command(reasoning_effort: str) -> tuple[str, ...]`, and `"grok"` in `router.SUPPORTED_VENDORS`.

Verified against the installed build on 2026-08-10: `grok -p <PROMPT>` is the single-turn form, `grok -p ""` fails with `Error: --single: prompt is empty`, `--reasoning-effort` reports `use one of: high, medium, low`, and `--permission-mode` lists `acceptEdits` among its values. `--sandbox` takes a profile whose vocabulary is not enumerated by `--help`, so the built-in leaves it at grok's own default rather than asserting a profile that was never verified.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_router.py`, after `AgyBuiltInRouteTests`:

```python
class GrokBuiltInRouteTests(unittest.TestCase):
    def test_every_tier_has_a_grok_route_that_carries_the_task_in_argv(self) -> None:
        """Breaks if a tier is missing or the built-in stops declaring its task slot."""
        for tier, effort in (("low", "low"), ("standard", "medium"), ("high", "high")):
            with self.subTest(tier=tier):
                route = select_tier_route(DEFAULT_ROUTES, tier, "grok")

                self.assertEqual(route.vendor, "grok")
                self.assertEqual(route.command[0], "grok")
                self.assertIn(router.TASK_PLACEHOLDER, route.command)
                index = route.command.index("--reasoning-effort")
                self.assertEqual(route.command[index + 1], effort)
                mode = route.command.index("--permission-mode")
                self.assertEqual(route.command[mode + 1], "acceptEdits")

    def test_grok_is_a_supported_source_vendor(self) -> None:
        """Breaks if the vendor label is rejected by the surfaces that gate routing."""
        self.assertIn("grok", SUPPORTED_VENDORS)

    def test_the_built_in_does_not_assert_an_unverified_sandbox_profile(self) -> None:
        """Breaks if a profile value nobody measured is baked into a shipped command."""
        route = select_tier_route(DEFAULT_ROUTES, "high", "grok")

        self.assertNotIn("--sandbox", route.command)
```

Add to `tests/test_triage.py`, inside `TriageCommandTests`:

```python
    def test_grok_has_no_reviewed_triage_adapter(self) -> None:
        """Breaks if an unreviewed adapter starts sending task text to a new vendor."""
        with self.assertRaises(TriageUnavailableError):
            triage_command("grok")

        descriptor = triage_descriptor("grok")
        self.assertFalse(descriptor["available"])
        self.assertEqual(descriptor["unavailable_reason"], "no_reviewed_triage_adapter")
        self.assertNotIn("command", descriptor)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_router.GrokBuiltInRouteTests tests.test_triage.TriageCommandTests -v`
Expected: FAIL with `RouteSelectionError` for the grok tiers.

- [ ] **Step 3: Add the vendor, its command builder, and its routes**

In `src/weightclass/router.py`, extend the vendor set:

```python
SUPPORTED_VENDORS: Final = frozenset({"claude", "codex", "agy", "grok"})
```

Add after `agy_command`:

```python
# grok 도 프롬프트를 stdin 에서 읽지 않는다. -p 가 단일 턴 프롬프트를 인자로 받고,
# 빈 문자열은 prompt is empty 로 닫힌다. --sandbox 는 프로필 어휘가 --help 에
# 열거되지 않아 측정하지 못했으므로 지정하지 않고 grok 자신의 기본값에 맡긴다.
# 검증하지 않은 값을 배포되는 명령에 박아 넣지 않는다.
GROK_COMMAND_PREFIX: Final = (
    "grok",
    "-p",
    TASK_PLACEHOLDER,
    "--permission-mode",
    "acceptEdits",
    "--reasoning-effort",
)


def grok_command(reasoning_effort: str) -> tuple[str, ...]:
    """Build the built-in Grok command for one reasoning effort label."""
    return GROK_COMMAND_PREFIX + (reasoning_effort,)
```

Append to `DEFAULT_ROUTES`, after the agy entries:

```python
    Route(
        route_id="grok-low",
        vendor="grok",
        workflow="",
        tier="low",
        command=grok_command("low"),
    ),
    Route(
        route_id="grok-standard",
        vendor="grok",
        workflow="",
        tier="standard",
        command=grok_command("medium"),
    ),
    Route(
        route_id="grok-high",
        vendor="grok",
        workflow="",
        tier="high",
        command=grok_command("high"),
    ),
```

In `src/weightclass/triage.py`:

```python
TRIAGE_UNAVAILABLE_REASONS: Final = {
    "codex": "no_no_tools_boundary",
    "agy": "no_reviewed_triage_adapter",
    "grok": "no_reviewed_triage_adapter",
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_router tests.test_triage -q`
Expected: OK.

- [ ] **Step 5: Run the full suite and the gates**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -t . -q`
Expected: OK, 653 tests.

Run: `ruff check . && ruff format --check . && mypy --strict src/weightclass tests`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/weightclass/router.py src/weightclass/triage.py tests/test_router.py tests/test_triage.py
git commit -m "feat: 내장 벤더로 grok 추가

설치된 빌드로 확인했다. grok -p 는 단일 턴 프롬프트를 인자로 받고 빈 문자열은
prompt is empty 로 닫히므로 태스크 자리를 argv 에 둔다. --reasoning-effort 는
high/medium/low 를, --permission-mode 는 acceptEdits 를 받는다.

--sandbox 는 프로필 어휘를 측정하지 못해 지정하지 않는다. 검증하지 않은 값을
배포되는 명령에 박아 넣지 않는다."
```

---

### Task 7: Document the exposure and the new vendors

**Files:**
- Modify: `README.md` (route documentation and the boundary list near line 630)
- Modify: `docs/protocol-v2-security.md` (residuals)
- Modify: `HANDOFF.md` (Goal, Current Status, Completed)

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: no code.

- [ ] **Step 1: Document the placeholder and the exposure in README**

In `README.md`, in the `## Override the routes` section, after the paragraph describing `command` tokens, add:

```markdown
A command may contain the reserved token `{{task}}` once, as a whole argument.
That route receives the task at that argv position and receives empty standard
input, instead of the default of the task on standard input. This exists for
agents that read a prompt only from their command line: `agy --print ""` and
`grok -p ""` both refuse an empty prompt and never read the pipe.

`wclass route` prints the command with `{{task}}` still in it and adds
`"task_delivery": "argv"`, so a review never contains task text and the
fingerprint does not change from one task to the next.

**Command lines are readable by every user on the machine.** A route that uses
`{{task}}` exposes the task to anyone who can run `ps` for as long as the child
runs. On a single-user machine this is inconsequential; on a shared host it is
not. Nothing weightclass can do removes this — it follows from how these agents
accept a prompt — so it is your decision each time you write `{{task}}` or
select an `agy` or `grok` built-in route.
```

In the boundary list near the end of `README.md`, after the bullet about built-in routes, add:

```markdown
- The built-in `agy` and `grok` routes deliver the task on the command line, so
  the `ps` exposure above applies to them. `claude` and `codex` routes deliver
  it on standard input and do not.
```

- [ ] **Step 2: Document the narrowed review guarantee**

In `docs/protocol-v2-security.md`, alongside the existing residuals, add:

```markdown
A route that declares the reserved `{{task}}` argv slot binds the shape of its
command, not the exact string reaching `execve`; one element is filled at run
time. Review and fingerprinting deliberately operate on the unsubstituted
command so neither carries task content, and so one review continues to bind
many runs. The task is visible in that child's command line to any local user
for the lifetime of the process. Standard-input delivery, which every `claude`
and `codex` route uses, has neither property.
```

- [ ] **Step 3: Update HANDOFF**

In `HANDOFF.md`, add to the Goal list:

```markdown
- Native routing supports `claude`, `codex`, `agy`, and `grok`. The last two
  accept a prompt only in argv, so they use the reserved `{{task}}` slot.
```

Add to `Completed`:

```markdown
- Native routing gained `agy` and `grok`. Both accept a prompt only in argv:
  `agy --print ""` returns `empty prompt` and `grok -p ""` returns
  `prompt is empty`, and neither reads the pipe. Their routes therefore declare
  the reserved `{{task}}` slot, which is filled immediately before spawn while
  review output and fingerprints keep using the unsubstituted command.
- Neither vendor has a triage adapter. `--ask-vendor` reports
  `no_reviewed_triage_adapter` for both, for the reason the qualification
  registry stays empty: an unreviewed adapter that hands untrusted task text to
  a tool under unknown permissions is worse than no adapter.
```

Add to `Blockers & Open Questions`:

```markdown
- `kimi`, `qwen`, and `deepseek` are not installed on the development machine.
  Their invocation was never measured, so no built-in command exists for them.
  Add one only after measuring it the way `agy` and `grok` were measured; a
  guessed command fails at the user's run rather than in this repository's tests.
- Built-in `agy` and `grok` routes put the task on the command line, where any
  local user can read it with `ps`. This follows from how those CLIs accept a
  prompt and cannot be removed while supporting them.
```

- [ ] **Step 4: Verify the documentation gates still pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest tests.test_completion_audit_v2 tests.test_protocol_v2_specification tests.test_legacy_contract -q`
Expected: OK.

Run: `git diff --check`
Expected: no output.

- [ ] **Step 5: Run the full suite and the gates**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -t . -q`
Expected: OK, 653 tests.

Run: `ruff check . && ruff format --check . && mypy --strict src/weightclass tests`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/protocol-v2-security.md HANDOFF.md
git commit -m "docs: argv 태스크 전달의 노출과 새 벤더를 문서화

명령줄은 같은 머신의 모든 사용자가 읽을 수 있다. {{task}} 를 쓰는 라우트는 자식이
사는 동안 태스크를 그들에게 노출한다. 이 CLI 들이 프롬프트를 받는 방식에서 오는
것이라 숨길 수 없으므로, 감출 대신 정확히 적는다.

argv 라우트에서 검토가 묶는 것은 명령의 모양이지 execve 에 닿는 문자열 전체가
아니라는 점도 다른 잔여 위험 옆에 명시한다."
```

---

## Verification before release

This plan produces a minor version under the `0.x` policy in `RELEASING.md`:
new command-line surface, nothing removed. Before tagging, run the release gates
from `RELEASING.md` — Python 3.10/3.13/3.14 with `ResourceWarning` as error,
both `umask 022` and `umask 002`, `python -m build`, `twine check --strict`, and
`verify_distribution_isolation.py --run-sdist-tests`.

Do not add `kimi`, `qwen`, or `deepseek`. They are not installed, their
invocation was never measured, and a guessed built-in command fails at the user's
run rather than in this repository's tests.
