import time
import unittest
from dataclasses import asdict

import weightclass.classification as classification
from weightclass.classification import (
    HIGH_SIGNAL_INFLECTIONS,
    HIGH_SIGNAL_NO_INFLECTION_RATIONALE,
    HIGH_SIGNALS,
    HIGH_TASK_CHARACTERS,
    LOW_TASK_CHARACTERS,
    MAX_TASK_CHARACTERS,
    InvalidTaskError,
    classify_task,
)


class ClassificationPolicyContractTests(unittest.TestCase):
    def test_policy_exposes_a_versioned_decision_without_changing_legacy_results(self) -> None:
        """Breaks if policy metadata is absent or leaks into classify_task results."""
        classify_with_reason = getattr(classification, "classify_task_with_reason", None)

        self.assertTrue(callable(classify_with_reason))
        self.assertEqual(getattr(classification, "CLASSIFICATION_POLICY_VERSION", None), "3")
        self.assertIsInstance(classification.classify_task("Fix the typo."), str)

    def test_high_signals_have_distinct_static_english_and_korean_reason_codes(self) -> None:
        """Breaks if a risk floor loses its category or returns matched task text."""
        cases = (
            ("Review the security boundary.", "high.complexity_signal"),
            ("보안 경계를 검토해줘.", "high.complexity_signal"),
            ("Prevent stored XSS in the account-settings form.", "high.risk_floor"),
            ("검색 엔드포인트의 SQL 인젝션을 수정해줘.", "high.risk_floor"),
            ("Stop unauthenticated users from reading invoices.", "high.risk_floor"),
            ("Prevent password enumeration in the reset flow.", "high.risk_floor"),
            ("비밀번호 재설정 흐름의 계정 열거를 막아줘.", "high.risk_floor"),
            ("An account is charged twice after checkout.", "high.harmful_outcome"),
            ("같은 작업이 재시작 뒤에 두 번 실행돼.", "high.harmful_outcome"),
        )

        for task, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                decision = classification.classify_task_with_reason(task)
                self.assertEqual((decision.tier, decision.reason_code), ("high", expected_reason))

    def test_mechanical_and_low_confidence_boundaries_have_static_reason_codes(self) -> None:
        """Breaks if low expands beyond mechanical work or ambiguity loses its code."""
        cases = (
            ("Fix the typo.", "low", "low.mechanical"),
            ("오타만 고쳐줘.", "low", "low.mechanical"),
            ("Update this helper.", "standard", "standard.not_clearly_mechanical"),
            ("이 도우미 함수를 수정해줘.", "standard", "standard.not_clearly_mechanical"),
            (
                "fix typo " + "x" * (LOW_TASK_CHARACTERS - len("fix typo ") + 1),
                "standard",
                "standard.not_clearly_mechanical",
            ),
        )

        for task, expected_tier, expected_reason in cases:
            with self.subTest(tier=expected_tier, reason=expected_reason):
                decision = classification.classify_task_with_reason(task)
                self.assertEqual(
                    (decision.tier, decision.reason_code),
                    (expected_tier, expected_reason),
                )

    def test_risk_floor_phrases_do_not_expand_to_neighboring_routine_words(self) -> None:
        """Breaks if risk-floor matching becomes substring based or overly broad."""
        cases = (
            "List signed-in users in the admin table.",
            "Enumerate the account rows in this unit test.",
            "Format the SQL example in the documentation.",
            "로그인한 사용자 목록의 문장부호를 고쳐줘.",
        )

        for position, task in enumerate(cases):
            with self.subTest(position=position):
                self.assertNotEqual(classification.classify_task(task), "high")

    def test_decision_contains_only_static_policy_metadata(self) -> None:
        """Breaks if task text, a hash, or a matched excerpt enters the decision."""
        unique_task = "Fix the typo in sentinel-private-input-9482."

        decision = asdict(classification.classify_task_with_reason(unique_task))

        self.assertEqual(
            decision,
            {
                "tier": "low",
                "reason_code": "low.mechanical",
                "policy_version": "3",
            },
        )

    def test_broad_complexity_reason_keeps_precedence_over_harmful_outcome(self) -> None:
        """Breaks if the existing high-signal precedence changes during the reason split."""
        decision = classification.classify_task_with_reason(
            "Review why the payment account was charged twice."
        )

        self.assertEqual((decision.tier, decision.reason_code), ("high", "high.complexity_signal"))


class SignalInflectionTests(unittest.TestCase):
    """굴절형이 시그널에서 빠져나가면 위험한 작업이 싼 티어로 떨어진다."""

    def test_every_ascii_high_signal_has_a_reviewed_inflection_entry(self) -> None:
        """Breaks if a HIGH signal is added without deciding its non-derivable forms.

        복수형 루프는 이 결함을 원리상 잡을 수 없다. 접미사 규칙이 이미 처리하는
        형태만 검사하기 때문이다. "deployment" 에서 "deploy" 를 규칙으로 유도할
        방법이 없으므로, 어형은 표로 못박고 그 표의 완전성을 여기서 강제한다.
        """
        ascii_signals = {signal for signal in HIGH_SIGNALS if signal.isascii()}

        self.assertEqual(set(HIGH_SIGNAL_INFLECTIONS), ascii_signals)
        # 빈 튜플은 결정이어야 한다. 이유가 없으면 나중에 누군가 오탐을 채워 넣는다.
        self.assertEqual(
            {signal for signal, forms in HIGH_SIGNAL_INFLECTIONS.items() if not forms},
            set(HIGH_SIGNAL_NO_INFLECTION_RATIONALE),
        )

    def test_declared_inflections_actually_reach_the_high_tier(self) -> None:
        """Breaks if an inflection is listed but never wired into the pattern."""
        for signal, forms in sorted(HIGH_SIGNAL_INFLECTIONS.items()):
            for form in forms:
                with self.subTest(signal=signal, form=form):
                    self.assertEqual(classify_task(f"Please handle the {form} here."), "high")

    def test_excluded_inflections_stay_out_of_the_high_tier(self) -> None:
        """Breaks if a form excluded for false positives gets added anyway."""
        excluded_but_ordinary = {
            "Make this method private.": "privacy",
            "Pay attention to the import order.": "payment",
            "Produce the weekly report.": "production",
        }

        for task, signal in excluded_but_ordinary.items():
            with self.subTest(signal=signal):
                self.assertIn(signal, HIGH_SIGNAL_NO_INFLECTION_RATIONALE)
                self.assertNotEqual(classify_task(task), "high")

    def test_every_ascii_high_signal_survives_its_plural(self) -> None:
        for signal in sorted(HIGH_SIGNALS):
            if not signal.isascii():
                continue
            with self.subTest(signal=signal):
                self.assertEqual(classify_task(f"Please review the {signal}s here."), "high")

    def test_an_inflected_high_signal_outranks_an_inflected_low_signal(self) -> None:
        cases = (
            "Reformatting the credentials file used by the deploy job.",
            "Renaming the payments table columns.",
            "Renaming the columns in the pending migrations.",
            "Fix the formatting of the authorizations audit log.",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), "high")

    def test_a_hyphenated_multi_word_high_signal_still_outranks_a_low_signal(self) -> None:
        """Breaks if a multi-word signal only matches its space-separated spelling."""
        cases = (
            "Reformat the data-loss recovery procedure.",
            "Rename the race-condition guard.",
            "Fix the data loss recovery procedure formatting.",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), "high")

    def test_common_inflections_of_high_signals_stay_high(self) -> None:
        cases = (
            "Rotate the credentials after a suspected leak.",
            "Review the payments flow.",
            "Refactoring this module.",
            "Fix race conditions in the worker pool.",
            "Review the pending migrations.",
            "Compare the two architectures.",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), "high")


class ThresholdTests(unittest.TestCase):
    """길이 임계값은 문서화된 계약이므로 경계 양쪽을 모두 고정한다."""

    def test_a_low_signal_stops_reaching_the_low_tier_past_its_length_limit(self) -> None:
        prefix = "fix typo "
        at_limit = prefix + "x" * (LOW_TASK_CHARACTERS - len(prefix))
        past_limit = prefix + "x" * (LOW_TASK_CHARACTERS - len(prefix) + 1)

        self.assertEqual(len(at_limit), LOW_TASK_CHARACTERS)
        self.assertEqual(classify_task(at_limit), "low")
        self.assertEqual(classify_task(past_limit), "standard")

    def test_length_alone_never_escalates_a_signal_free_task(self) -> None:
        """Breaks if length goes back to being read as difficulty.

        긴 설명은 "기계적이지 않다"의 증거일 뿐 "위험하다"의 증거가 아니다. 예전
        정책은 이 둘을 같은 것으로 취급해서, 파일 목록을 붙여넣은 단순 작업을 최고
        비용 경로로 보냈다. 자세히 쓸수록 비싸지는 인센티브 역전이 회귀하면 여기서
        깨진다.
        """
        below_limit = "x" * (HIGH_TASK_CHARACTERS - 1)
        at_limit = "x" * HIGH_TASK_CHARACTERS

        self.assertEqual(classify_task(below_limit), "standard")
        self.assertEqual(
            classification.classify_task_with_reason(at_limit),
            classification.ClassificationDecision("standard", "standard.length_floor"),
        )

    def test_length_still_disqualifies_the_cheap_tier(self) -> None:
        """Breaks if a long task can reach low just because it carries a LOW signal."""
        long_mechanical = "fix the typo. " + "x " * HIGH_TASK_CHARACTERS

        self.assertEqual(classify_task(long_mechanical), "standard")

    def test_rejects_a_task_past_the_maximum_character_limit(self) -> None:
        self.assertEqual(classify_task("x" * MAX_TASK_CHARACTERS), "standard")
        with self.assertRaises(InvalidTaskError):
            classify_task("x" * (MAX_TASK_CHARACTERS + 1))

    def test_high_signals_are_found_past_the_pattern_scan_bound(self) -> None:
        """Breaks if the backtracking slice starts hiding risk vocabulary.

        되추적 상한을 길이 바닥에서 슬라이스로 옮기면서 생긴 구멍이다. 시그널까지
        잘라서 검사하면 긴 작업 후반부의 "security" 가 통째로 사라지고, 예전에는
        길이 바닥이 가려주던 그 구멍이 이제는 그대로 비용 오분류가 된다.
        """
        buried = "x " * classification.PATTERN_SCAN_CHARACTERS + "review the security boundary."

        self.assertGreater(len(buried), classification.PATTERN_SCAN_CHARACTERS)
        self.assertEqual(classify_task(buried), "high")


class ClassificationRegressionTests(unittest.TestCase):
    def test_classifies_explicit_high_impact_outcomes_as_high(self) -> None:
        """Breaks if costly duplicate-work or balance-integrity failures stay standard.

        These cases intentionally describe the harmful outcome without relying on
        a technology-specific keyword such as ``payment`` or ``concurrency``.
        Replacing the outcome patterns with ordinary keywords must make this
        test fail.
        """
        cases = (
            "An account is charged twice after one checkout.",
            "I was charged twice after checkout.",
            "My card was charged twice.",
            "Workers run the same job twice after a restart.",
            "Balances sometimes become negative after transfers.",
            "같은 작업이 재시작 뒤에 두 번 실행돼.",
            "잔액이 가끔 음수가 돼.",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), "high")

    def test_harmful_outcome_patterns_remain_bounded_but_match_across_newlines(self) -> None:
        """Breaks if a line wrap hides an otherwise identical costly outcome."""
        cases = (
            "An account was charged\ntwice after checkout.",
            "The same job sometimes\nruns twice.",
            "The balance\nbecame negative.",
            "같은 작업이\n두 번 실행돼.",
            "잔액이 가끔\n음수가 돼.",
            "잔액이 음수로\n내려갔어.",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), "high")

    def test_duplicate_work_qualifier_order_does_not_change_the_tier(self) -> None:
        """Breaks if a qualifier is visible only after the event's first token."""
        cases = (
            "Sometimes the same job runs twice.",
            "The same job sometimes runs twice.",
            "The same job runs twice unexpectedly.",
            "Workers run the same job twice after a restart.",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), "high")

    def test_duplicate_multiplicity_anchor_does_not_qualify_itself(self) -> None:
        """Breaks if `multiple` in `multiple times` invents instability evidence."""
        self.assertEqual(classify_task("The same job runs multiple times."), "standard")

    def test_does_not_escalate_mentions_without_a_high_impact_outcome(self) -> None:
        """Breaks if broad outcome words turn routine presentation work into high."""
        cases = (
            "Show negative balances in red in the dashboard.",
            "Run the report job twice in the integration test to verify idempotency.",
            "Configure the worker to run a report job twice for every input.",
            "The same job runs twice in an integration test to verify idempotency.",
            "The same job runs twice when running an integration test.",
            "Workers run twice daily; render the same job title in the dashboard.",
            "음수 잔액의 텍스트 색상을 바꿔줘.",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), "standard")

    def test_classifies_operational_privacy_and_credential_tasks_as_high(self) -> None:
        cases = {
            "Plan a rollback after the failed deployment.": "high",
            "Review the privacy implications for customer records.": "high",
            "Rotate the service credential after a suspected leak.": "high",
            "배포 롤백 절차를 검토해줘.": "high",
            "개인정보 처리 범위를 검토해줘.": "high",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)

    def test_classifies_short_nonfunctional_edits_as_low(self) -> None:
        cases = {
            "Normalize whitespace in this README.": "low",
            "README 제목의 문장부호만 고쳐줘.": "low",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)

    def test_does_not_escalate_on_a_signal_embedded_in_a_longer_word(self) -> None:
        """Breaks if HIGH signals go back to matching without word boundaries."""
        cases = {
            "Write reproduction steps for this crash.": "standard",
            "Update the reproduction script comment.": "low",
            "Add a focused unit test for this formatter.": "standard",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)

    def test_classifies_inflected_nonfunctional_edits_as_low(self) -> None:
        """Breaks if only the bare stem of a LOW signal reaches the cheap tier."""
        cases = {
            "Fix the typos in the changelog.": "low",
            "Fix the formatting of this markdown table.": "low",
            "Reformat this file.": "low",
            "Renaming the variable userId to accountId.": "low",
            "Strip trailing whitespaces from the file.": "low",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)

    def test_classifies_common_korean_spellings_consistently(self) -> None:
        """Breaks if a Korean transliteration variant misses its tier."""
        cases = {
            "이 함수를 리팩토링 해줘.": "high",
            "이 함수를 리팩터링 해줘.": "high",
            "변수 이름변경 해줘.": "low",
            "변수명 리네임 해줘.": "low",
            "코드 포매팅만 정리해줘.": "low",
            "본문 띄어쓰기만 고쳐줘.": "low",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)

    def test_lets_a_high_signal_win_over_a_low_signal(self) -> None:
        """Breaks if the conservative high-wins precedence is reversed."""
        cases = {
            "Fix a typo in the authorization error message.": "high",
            "결제 화면 문구 오타 수정.": "high",
        }

        for task, expected_tier in cases.items():
            with self.subTest(task=task):
                self.assertEqual(classify_task(task), expected_tier)


# 상위 티어 패턴들은 `[\s\S]{0,80}` 같은 경계 있는 와일드카드를 여러 겹 중첩한다.
# 매칭 실패 시 되추적 비용이 입력 길이에 대해 준-2차로 늘어나므로, 정규식이 보는
# 최대 길이를 무엇이 닫고 있는지가 성능 계약이다. 지금은 길이 바닥이 닫는다.
_BACKTRACKING_BAIT: tuple[str, ...] = (
    "the same task runs again and ",
    "user is charged and ",
    "job runs same task ",
    "balance is turned and ",
)


class CheapTierRecallTests(unittest.TestCase):
    """저비용 경로가 실제로 열려야 라우팅이 비용을 낮춘다.

    화이트리스트에 없는 어휘라는 이유만으로 기계적 작업이 standard 로 가면, 절감
    레버는 사실상 닫힌 채로 상위 오분류만 남는다. 여기 있는 사례들은 전부 그
    실패 모드에서 나왔다.
    """

    def test_mechanical_action_and_object_pairs_reach_the_cheap_tier(self) -> None:
        """Breaks if the action/object co-occurrence rule stops firing."""
        cases = (
            "sort the imports in main.py",
            "remove the unused import in main.py",
            "add a docstring to the parse_config function",
            "changelog 에 한 줄 추가해줘",
            "이 로그 메시지 문구만 바꿔줘",
        )

        for task in cases:
            with self.subTest(task=task):
                decision = classification.classify_task_with_reason(task)
                self.assertEqual(
                    (decision.tier, decision.reason_code), ("low", "low.mechanical_pair")
                )

    def test_literal_substitution_requests_reach_the_cheap_tier(self) -> None:
        """Breaks if stated target states stop counting as mechanical work."""
        cases = (
            "change the default page size on get /orders from 20 to 50",
            "sort the deps and devdeps in package.json alphabetically",
            "config/logging.yaml에서 기본 로그 레벨 debug에서 info로 내려줘.",
            "readme 설치 섹션의 npm 명령어들 전부 pnpm으로 바꿔줘.",
        )

        for task in cases:
            with self.subTest(task=task):
                decision = classification.classify_task_with_reason(task)
                self.assertEqual((decision.tier, decision.reason_code), ("low", "low.substitution"))

    def test_substitution_requires_a_literal_and_not_a_described_feature(self) -> None:
        """Breaks if 'X 로 바꿔' matches a feature description.

        치환 대상이 리터럴이 아니면 그것은 구현 요청이다. 이 구분이 사라지면 기능
        구현이 최저 비용 경로로 떨어진다.
        """
        task = "상품 목록 페이지를 무한 스크롤로 바꿔줘. 지금은 하단 페이지네이션 버튼이야."

        self.assertEqual(classification.classify_task(task), "standard")

    def test_a_mechanical_fragment_does_not_downgrade_a_larger_request(self) -> None:
        """Breaks if low evidence in one clause decides a multi-instruction request.

        기계적 증거가 요청의 한 조각에만 걸려 있어도 규칙은 전체를 low 로 내렸다.
        실제 작업이 나머지 절에 있으면 어려운 일이 최저 비용 경로로 떨어진다.
        """
        cases = (
            "remove the debug comment, then rewrite the retry backoff so it stops hammering",
            "add a docstring to parse_config and make the parser reject unknown keys",
            "sort the imports; then split this module into two",
            "이 주석 지우고 재시도 간격 계산도 다시 짜줘",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classification.classify_task(task), "standard")

    def test_an_ordinary_two_sentence_request_is_not_multiple_instructions(self) -> None:
        """Breaks if the guard goes back to treating a full stop as a second request.

        요청 프롬프트는 거의 전부 "무엇이 문제다. 이렇게 고쳐라" 형태다. 마침표를
        지시 경계로 읽으면 저비용 규칙 전체가 사실상 꺼진다. blind 평가 36개
        세트에서 예전 패턴이 34개에 걸렸고 대부분이 평범한 마침표였다.
        """
        cases = (
            "with_retry retries immediately with no pause. Change the default delay to 0.05.",
            "Worker count is unbounded. Add a version constant to config.py.",
            "로그에 시각이 없어 언제인지 알 수가 없어. 로그 메시지 앞을 바꿔줘.",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertFalse(classification._has_multiple_instructions(task.casefold()))

    def test_a_korean_auxiliary_verb_is_not_a_second_instruction(self) -> None:
        """Breaks if `~고` matches a continuation instead of a conjunction.

        "노출되고 있어" 와 "확인하고 싶은데" 는 명령 두 개가 아니라 보조 용언이다.
        """
        for task in ("이메일이 그대로 노출되고 있어", "무엇이 있는지 확인하고 싶은데"):
            with self.subTest(task=task):
                self.assertFalse(classification._has_multiple_instructions(task))
        self.assertTrue(classification._has_multiple_instructions("이 주석 지우고 로직도 고쳐줘"))

    def test_a_mechanical_pair_must_apply_to_the_same_request(self) -> None:
        """Breaks if the action and object can be matched anywhere in the text.

        만장일치 high 였던 요청이 "파일 이름의 stem"(목적어)과 멀리 떨어진 "전부
        지워져"(동사)로 low 가 되었다. 어느 쪽도 요청의 일부가 아니라 문제 서술
        이었다.
        """
        distant = (
            "keep 에는 보존할 issue id 를 넣는데 purge 는 그걸 파일 이름의 stem 과 그대로 "
            "비교해. 그래서 i1.backup.txt 나 i1.draft.md 처럼 보존 대상 issue 에서 파생된 "
            "파일은 stem 이 달라서 전부 지워져. 실제로 이렇게 백업본을 잃은 적이 있어. "
            "keep 에 있는 issue 에 속한 파일은 파생 파일까지 남도록 고쳐줘."
        )

        self.assertEqual(classification.classify_task(distant), "standard")
        self.assertEqual(classification.classify_task("이 로그 메시지 문구만 바꿔줘"), "low")

    def test_substituting_a_component_is_not_substituting_a_value(self) -> None:
        """Breaks if `from X to Y` alone can downgrade a component swap.

        값 교체는 판단할 것이 남아 있지 않지만 구성 요소 교체는 그렇지 않다.
        영어 from/to 패턴만 기계적 동사를 요구하지 않아 가장 넓었다.
        """
        self.assertEqual(
            classification.classify_task("switch the cache from redis to memcached"), "standard"
        )
        self.assertEqual(
            classification.classify_task(
                "move the session store from local disk to the shared cluster"
            ),
            "standard",
        )
        value_swap = classification.classify_task_with_reason(
            "change the default page size on get /orders from 20 to 50"
        )
        self.assertEqual((value_swap.tier, value_swap.reason_code), ("low", "low.substitution"))

    def test_risk_vocabulary_still_wins_over_every_cheap_rule(self) -> None:
        """Breaks if a cheap rule is ever consulted before the high-tier checks."""
        cases = (
            "rename the auth token comment from a to b",
            "결제 로그 메시지를 info로 바꿔줘",
            "remove the unused import in the migration script",
        )

        for task in cases:
            with self.subTest(task=task):
                self.assertEqual(classification.classify_task(task), "high")


class BacktrackingBoundTests(unittest.TestCase):
    def test_nested_wildcard_patterns_never_see_more_than_the_scan_bound(self) -> None:
        """Breaks if the bounded-wildcard patterns are exposed to 20,000 characters at once.

        되추적 상한을 길이 바닥이 아니라 창 크기가 맡는다. 길이 바닥은 이제 티어를
        올리지 않으므로, 이 계약이 깨지면 적대적 입력 하나가 20,000자에 대해 준-2차
        되추적을 태울 수 있다.
        """
        windows = list(classification._scan_windows("x" * MAX_TASK_CHARACTERS))

        self.assertTrue(windows)
        self.assertLessEqual(
            max(len(window) for window in windows),
            classification.PATTERN_SCAN_CHARACTERS,
        )

    def test_scan_windows_cover_the_whole_task_with_overlap(self) -> None:
        """Breaks if windowing leaves a gap a harmful outcome could hide in.

        창이 겹치지 않으면 경계에 걸친 서술이 어느 창에도 온전히 들어가지 않는다.
        """
        task = "".join(chr(ord("a") + index % 26) for index in range(MAX_TASK_CHARACTERS))
        windows = list(classification._scan_windows(task))

        self.assertEqual(windows[0], task[: classification.PATTERN_SCAN_CHARACTERS])
        self.assertTrue(task.endswith(windows[-1]))
        step = classification.PATTERN_SCAN_CHARACTERS - classification.PATTERN_SCAN_OVERLAP
        self.assertGreater(classification.PATTERN_SCAN_OVERLAP, 0)
        for earlier, later in zip(windows, windows[1:], strict=False):
            self.assertGreaterEqual(len(earlier) - step, classification.PATTERN_SCAN_OVERLAP - 1)
            self.assertTrue(earlier[step:] and later.startswith(earlier[step:][: len(later)]))

    def test_padding_cannot_hide_a_harmful_outcome(self) -> None:
        """Breaks if leading filler can deterministically downgrade a costly outcome.

        길이 바닥이 티어를 올리던 동안에는 이 구멍이 가려져 있었다. 길이가 더 이상
        올리지 않게 된 뒤로는, 앞을 채우는 것만으로 뒤에 있는 유해 결과 서술을
        숨겨 최고 위험 작업을 standard 로 내릴 수 있다.
        """
        outcome = "an account is charged twice after checkout."
        korean = "잔액이 가끔 음수로 내려가요."

        for filler in (0, 1_300, MAX_TASK_CHARACTERS - len(outcome) - 1):
            with self.subTest(filler=filler):
                self.assertEqual(classify_task("x" * filler + " " + outcome), "high")
        self.assertEqual(classify_task("가" * 3_000 + " " + korean), "high")

    def test_patterns_stay_cheap_at_the_maximum_accepted_input(self) -> None:
        """Breaks if windowing the whole task makes hostile input superlinear.

        결과 패턴이 이제 앞부분이 아니라 전체를 훑는다. 창당 비용이 유계라는 주장이
        실제 상한 길이에서 검증되지 않으면, 상한을 올리거나 창 크기를 키울 때 비용
        폭발을 놓친다.
        """
        for bait in _BACKTRACKING_BAIT:
            with self.subTest(bait=bait):
                hostile = (bait * (MAX_TASK_CHARACTERS // len(bait) + 1))[:MAX_TASK_CHARACTERS]
                started = time.perf_counter()
                classify_task(hostile)
                elapsed = time.perf_counter() - started

                self.assertLess(elapsed, 5.0)

    def test_patterns_stay_cheap_at_the_longest_input_they_can_reach(self) -> None:
        """Breaks if backtracking becomes superlinear just below the length floor."""
        longest_reachable = HIGH_TASK_CHARACTERS - 1

        for bait in _BACKTRACKING_BAIT:
            with self.subTest(bait=bait):
                hostile = (bait * (longest_reachable // len(bait) + 1))[:longest_reachable]
                started = time.perf_counter()
                classify_task(hostile)
                elapsed = time.perf_counter() - started

                # 실측 최악값은 밀리초 단위다. 이 상한은 느린 러너를 위한 여유이며,
                # 진짜 되추적 폭발(초 단위)만 잡도록 넉넉하게 잡았다.
                self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
