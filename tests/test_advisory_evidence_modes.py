from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "src" / "weightclass" / "advisory"
RUNNER = TOOLS / "speculative_run.py"
REPORT = TOOLS / "speculative_report.py"
CONTRACT = TOOLS / "advisory_evidence_contract.py"
CAMPAIGN = TOOLS / "advisory_campaign.py"
ROUTES = TOOLS / "advisory_routes.py"
BRAINSTORM_ASSESSMENT = ROOT / "docs" / "advisory-brainstorming-assessment.md"
for directory in (str(ROOT), str(TOOLS)):
    if directory not in sys.path:
        sys.path.insert(0, directory)

REPOSITORY_TOOLS_AVAILABLE = all(
    path.is_file() for path in (RUNNER, REPORT, CONTRACT, CAMPAIGN, ROUTES)
)


def load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def review_result(summary: str = "REVIEW-SUMMARY") -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "review",
        "summary": summary,
        "findings": [],
        "limitations": [],
    }


def research_result() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "research",
        "question": "Which option is supported?",
        "summary": "One bounded synthesis.",
        "claims": [
            {
                "claim": "The evidence supports option A.",
                "status": "supported",
                "confidence": "high",
                "evidence": ["source:1"],
                "counterevidence": [],
            }
        ],
        "limitations": [],
    }


def diagnosis_result() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "diagnosis",
        "symptom": "The bounded check fails.",
        "summary": "The parser rejects the input before execution.",
        "hypotheses": [
            {
                "cause": "The input is malformed.",
                "status": "confirmed",
                "confidence": "high",
                "evidence": ["reproduction exits 2"],
                "counterevidence": [],
            }
        ],
        "reproduction": ["run the focused parser test"],
        "limitations": [],
    }


def design_result(summary: str = "DESIGN-SUMMARY") -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "design",
        "problem": "The current interface obscures the primary action.",
        "summary": summary,
        "principles": ["Preserve a clear visual hierarchy."],
        "options": [
            {
                "title": "Clarify the primary action",
                "rationale": "The repository has competing actions with equal emphasis.",
                "evidence": ["src/ui/example.tsx:10 renders both actions identically."],
                "strengths": ["Makes the intended next step easier to identify."],
                "risks": ["May reduce discovery of the secondary action."],
                "affected_surfaces": ["primary form"],
            }
        ],
        "recommendation": "Adopt the hierarchy change after responsive review.",
        "acceptance_criteria": ["Primary action is identifiable without color alone."],
        "validation": ["Check keyboard, contrast, and narrow viewport behavior."],
        "limitations": [],
    }


@unittest.skipUnless(REPOSITORY_TOOLS_AVAILABLE, "repository-only advisory tools unavailable")
class EvidenceContractTests(unittest.TestCase):
    def test_mode_contracts_are_closed_bounded_and_prompted(self) -> None:
        self.assertTrue(CONTRACT.is_file())
        contract = load_module(CONTRACT, "prospective_evidence_contract")
        examples = {
            "review": review_result(),
            "research": research_result(),
            "diagnosis": diagnosis_result(),
        }
        for mode, value in examples.items():
            with self.subTest(mode=mode):
                encoded = json.dumps(value)
                self.assertEqual(contract.parse_evidence_result(encoded, mode), value)
                prompt = contract.build_evidence_prompt("PRIVATE TASK", mode)
                self.assertIn("Return exactly one JSON object", prompt)
                self.assertIn(f'"mode":"{mode}"', prompt)
                self.assertIn("PRIVATE TASK", prompt)
                self.assertIn("Do not edit", prompt)

                invented = dict(value)
                invented["task_hash"] = "forbidden"
                with self.assertRaisesRegex(contract.EvidenceResultError, "^$"):
                    contract.parse_evidence_result(json.dumps(invented), mode)

        duplicate = '{"schema_version":1,"schema_version":1}'
        with self.assertRaisesRegex(contract.EvidenceResultError, "^$"):
            contract.parse_evidence_result(duplicate, "review")
        with self.assertRaisesRegex(contract.EvidenceResultError, "^$"):
            contract.parse_evidence_result(json.dumps(review_result()), "research")
        with self.assertRaisesRegex(contract.EvidenceResultError, "^$"):
            contract.parse_evidence_result("x" * (contract.MAX_EVIDENCE_RESULT_BYTES + 1), "review")

    def test_design_contract_is_closed_bounded_and_prompted(self) -> None:
        contract = load_module(CONTRACT, "prospective_design_contract")
        design = design_result()
        encoded = json.dumps(design)
        self.assertEqual(contract.parse_evidence_result(encoded, "design"), design)
        prompt = contract.build_evidence_prompt("PRIVATE DESIGN TASK", "design")
        self.assertIn('"mode":"design"', prompt)
        self.assertIn("PRIVATE DESIGN TASK", prompt)
        self.assertIn("Do not edit", prompt)

        invented = dict(design)
        invented["task_hash"] = "forbidden"
        with self.assertRaisesRegex(contract.EvidenceResultError, "^$"):
            contract.parse_evidence_result(json.dumps(invented), "design")

        options = design["options"]
        assert isinstance(options, list)
        option = options[0]
        assert isinstance(option, dict)
        option["risks"] = []
        with self.assertRaisesRegex(contract.EvidenceResultError, "^$"):
            contract.parse_evidence_result(json.dumps(design), "design")

    def test_review_finding_and_claim_shapes_reject_unknown_or_unbounded_values(self) -> None:
        contract = load_module(CONTRACT, "prospective_evidence_shapes")
        review = review_result()
        review["findings"] = [
            {
                "title": "Unsafe boundary",
                "severity": "medium",
                "confidence": "high",
                "disposition": "reportable",
                "locations": ["src/example.py:10"],
                "evidence": ["input reaches sink"],
                "counterevidence": [],
                "recommendation": "Validate before the sink.",
            }
        ]
        self.assertEqual(
            contract.parse_evidence_result(json.dumps(review), "review"),
            review,
        )
        findings = review["findings"]
        assert isinstance(findings, list)
        finding = findings[0]
        assert isinstance(finding, dict)
        finding["severity"] = "urgent"
        with self.assertRaisesRegex(contract.EvidenceResultError, "^$"):
            contract.parse_evidence_result(json.dumps(review), "review")

        research = research_result()
        claims = research["claims"]
        assert isinstance(claims, list)
        claim = claims[0]
        assert isinstance(claim, dict)
        claim["evidence"] = ["x" * (contract.MAX_EVIDENCE_STRING_BYTES + 1)]
        with self.assertRaisesRegex(contract.EvidenceResultError, "^$"):
            contract.parse_evidence_result(json.dumps(research), "research")

    def test_brainstorming_assessment_keeps_divergence_separate_from_shape_b(self) -> None:
        self.assertTrue(BRAINSTORM_ASSESSMENT.is_file())
        assessment = BRAINSTORM_ASSESSMENT.read_text(encoding="utf-8")
        for required in (
            "Decision: experiment before promotion",
            "generator-critic",
            "constraint compliance",
            "idea diversity",
            "human preference",
        ):
            self.assertIn(required, assessment)


@unittest.skipUnless(REPOSITORY_TOOLS_AVAILABLE, "repository-only advisory tools unavailable")
class EvidenceCampaignAndRouteTests(unittest.TestCase):
    def profile(self, vendor: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "vendor": vendor,
            "models": {"cheap": "c", "advisor": "a", "expensive": "e"},
            "efforts": {"cheap": "high", "advisor": "high", "expensive": "high"},
        }

    def test_read_only_profiles_remove_executor_write_authority(self) -> None:
        routes = load_module(ROUTES, "prospective_evidence_routes")
        claude = routes.build_routes(self.profile("claude"), read_only_executors=True)
        for role, command in zip(("cheap", "advisor", "expensive"), claude, strict=True):
            self.assertNotIn("Edit", command[command.index("--tools") + 1].split(","))
            permission = command[command.index("--permission-mode") + 1]
            if role == "advisor":
                self.assertEqual(permission, "plan")
                self.assertNotIn("--json-schema", command)
            else:
                self.assertEqual(permission, "dontAsk")
                schema = json.loads(command[command.index("--json-schema") + 1])
                self.assertEqual(schema["required"], ["schema_version", "mode"])
        codex = routes.build_routes(self.profile("codex"), read_only_executors=True)
        for command in codex:
            self.assertEqual(command[command.index("--sandbox") + 1], "read-only")

    def test_schema_two_campaign_binds_workflow_without_rewriting_schema_one(self) -> None:
        campaign = load_module(CAMPAIGN, "prospective_evidence_campaign")
        with tempfile.TemporaryDirectory() as directory:
            verify = Path(directory) / "verify"
            verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify.chmod(0o700)
            common = {
                "arm": "shape_b",
                "planned_tasks": 12,
                "max_tasks": 20,
                "cost_basis": "vendor",
                "cheap": ["codex", "cheap"],
                "expensive": ["codex", "strong"],
                "advisor": ["codex", "advisor"],
                "advisor_context": "prompt",
                "verify": verify,
                "prices": None,
            }
            implementation = campaign.build_manifest(**common)
            evidence = campaign.build_manifest(**common, workflow="review")
            path = Path(directory) / "campaign.json"
            campaign.write_manifest(path, evidence)
            loaded = campaign.load_manifest(path)

        self.assertEqual(implementation["schema_version"], 1)
        self.assertNotIn("workflow", implementation)
        self.assertEqual(evidence["schema_version"], 2)
        self.assertEqual(evidence["workflow"], "review")
        self.assertEqual(loaded, evidence)
        self.assertEqual(campaign.record_binding(evidence, 1)["workflow"], "review")
        with self.assertRaisesRegex(campaign.CampaignError, "^campaign_record_binding_mismatch$"):
            campaign.validate_record_bindings(
                evidence,
                [
                    {
                        "campaign": campaign.record_binding(evidence, 1),
                        "workflow": "research",
                    }
                ],
            )
        with self.assertRaisesRegex(campaign.CampaignError, "^$"):
            campaign.validate_run_configuration(
                evidence,
                cheap=common["cheap"],
                expensive=common["expensive"],
                advisor=common["advisor"],
                advise_first=False,
                advise_on_failure=True,
                advisor_context="prompt",
                verify=verify,
                prices=None,
                prefer_prices=False,
                sample_ordinal=1,
                workflow="research",
            )

    def test_schema_two_campaign_accepts_and_binds_design_workflow(self) -> None:
        campaign = load_module(CAMPAIGN, "prospective_design_campaign")
        with tempfile.TemporaryDirectory() as directory:
            verify = Path(directory) / "verify"
            verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify.chmod(0o700)
            manifest = campaign.build_manifest(
                arm="shape_b",
                planned_tasks=12,
                max_tasks=20,
                cost_basis="vendor",
                cheap=["codex", "cheap"],
                expensive=["codex", "strong"],
                advisor=["codex", "advisor"],
                advisor_context="prompt",
                verify=verify,
                prices=None,
                workflow="design",
            )

        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["workflow"], "design")
        self.assertEqual(campaign.record_binding(manifest, 1)["workflow"], "design")


@unittest.skipUnless(REPOSITORY_TOOLS_AVAILABLE, "repository-only advisory tools unavailable")
class EvidenceRunnerTests(unittest.TestCase):
    def repository(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        return repo

    def evaluator(
        self,
        root: Path,
        accepted_summary: str = "REVIEW-SUMMARY",
        workflow: str = "review",
    ) -> Path:
        path = root / "evaluate.py"
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "value=json.load(sys.stdin)\n"
            f"ok=value.get('summary') == {accepted_summary!r}\n"
            f"ok=ok and os.environ.get('WCLASS_ADVISORY_WORKFLOW') == {workflow!r}\n"
            "raise SystemExit(0 if ok else 1)\n",
            encoding="utf-8",
        )
        path.chmod(0o700)
        return path

    def executor(self, result: dict[str, object], *, edit: bool = False) -> str:
        program = "import json,sys;sys.stdin.read();"
        if edit:
            program += "open('CHANGED.txt','w').write('changed');"
        program += f"print(json.dumps({result!r}))"
        return shlex.join([sys.executable, "-c", program])

    def run_runner(
        self,
        root: Path,
        *,
        cheap: str,
        expensive: str | None = None,
        advisor: str | None = None,
        advise_on_failure: bool = False,
        workflow: str = "review",
        accepted_summary: str = "REVIEW-SUMMARY",
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        repo = self.repository(root)
        task = root / "task.txt"
        task.write_text("PRIVATE-TASK-MATERIAL", encoding="utf-8")
        task.chmod(0o600)
        out = root / "out"
        command = [
            sys.executable,
            str(RUNNER),
            "--workflow",
            workflow,
            "--repo",
            str(repo),
            "--task-file",
            str(task),
            "--cheap",
            cheap,
            "--expensive",
            expensive or cheap,
            "--verify",
            str(self.evaluator(root, accepted_summary, workflow)),
            "--out-dir",
            str(out),
        ]
        if advisor is not None:
            command.extend(["--advisor", advisor])
        if advise_on_failure:
            command.append("--advise-on-failure")
        completed = subprocess.run(command, capture_output=True, check=False, text=True)
        return completed, repo, out

    def test_read_only_success_is_printed_but_never_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed, repo, out = self.run_runner(root, cheap=self.executor(review_result()))
            log = (out / "runs.jsonl").read_text(encoding="utf-8")
            record = json.loads(log)
            persisted = "".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in out.rglob("*")
                if path.is_file()
            )
            repo_status = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("REVIEW-SUMMARY", completed.stdout)
        self.assertNotIn("PRIVATE-TASK-MATERIAL", completed.stdout + completed.stderr)
        self.assertEqual(record["workflow"], "review")
        self.assertTrue(record["cheap"]["accepted"])
        self.assertFalse(record["cheap"]["made_changes"])
        self.assertGreater(record["cheap"]["result_chars"], 0)
        self.assertNotIn("REVIEW-SUMMARY", persisted)
        self.assertEqual(list(out.glob("*.patch")), [])
        self.assertEqual(repo_status, "")

    def test_design_success_uses_the_same_transient_read_only_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed, repo, out = self.run_runner(
                Path(directory),
                cheap=self.executor(design_result()),
                workflow="design",
                accepted_summary="DESIGN-SUMMARY",
            )
            log = (out / "runs.jsonl").read_text(encoding="utf-8")
            record = json.loads(log)
            persisted = "".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in out.rglob("*")
                if path.is_file()
            )
            repo_status = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("DESIGN-SUMMARY", completed.stdout)
        self.assertEqual(record["workflow"], "design")
        self.assertNotIn("DESIGN-SUMMARY", persisted)
        self.assertEqual(list(out.glob("*.patch")), [])
        self.assertEqual(repo_status, "")

    def test_repository_edit_is_rejected_even_when_result_and_verifier_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = self.executor(review_result(), edit=True)
            completed, _repo, out = self.run_runner(root, cheap=command, expensive=command)
            record = json.loads((out / "runs.jsonl").read_text(encoding="utf-8"))

        self.assertEqual(completed.returncode, 1)
        self.assertFalse(record["cheap"]["accepted"])
        self.assertIn("read-only", record["cheap"]["error"])
        self.assertFalse(record["expensive"]["accepted"])
        self.assertNotIn("REVIEW-SUMMARY", completed.stdout)
        self.assertEqual(list(out.glob("*.patch")), [])

    def test_excluded_scaffolding_is_also_a_read_only_violation(self) -> None:
        result = review_result()
        program = (
            "import json,os,sys;sys.stdin.read();"
            "os.mkdir('.claude');open('.claude/state','w').write('changed');"
            f"print(json.dumps({result!r}))"
        )
        command = shlex.join([sys.executable, "-c", program])
        with tempfile.TemporaryDirectory() as directory:
            completed, _repo, out = self.run_runner(
                Path(directory), cheap=command, expensive=command
            )
            record = json.loads((out / "runs.jsonl").read_text(encoding="utf-8"))

        self.assertEqual(completed.returncode, 1)
        self.assertIn("excluded", record["cheap"]["error"])
        self.assertFalse(record["cheap"]["accepted"])

    def test_failed_evaluation_can_be_rescued_without_persisting_any_result(self) -> None:
        first = review_result("FIRST-FAIL")
        retry = review_result("REVIEW-SUMMARY")
        program = (
            "import json,sys;task=sys.stdin.read();"
            f"value={retry!r} if 'ADVICE FROM A SECOND MODEL' in task else {first!r};"
            "print(json.dumps(value))"
        )
        executor = shlex.join([sys.executable, "-c", program])
        advisor = shlex.join([sys.executable, "-c", "import sys;sys.stdin.read();print('revise')"])
        with tempfile.TemporaryDirectory() as directory:
            completed, _repo, out = self.run_runner(
                Path(directory),
                cheap=executor,
                advisor=advisor,
                advise_on_failure=True,
            )
            log = (out / "runs.jsonl").read_text(encoding="utf-8")
            record = json.loads(log)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(record["cheap"]["accepted"])
        self.assertIsNotNone(record["advice_failure"])
        self.assertTrue(record["retry"]["accepted"])
        self.assertFalse(record["escalated"])
        self.assertIn("REVIEW-SUMMARY", completed.stdout)
        for forbidden in ("FIRST-FAIL", "REVIEW-SUMMARY", "revise"):
            self.assertNotIn(forbidden, log)

    def test_provider_json_envelopes_extract_the_strict_result(self) -> None:
        self.assertTrue(CONTRACT.is_file())
        runner = load_module(RUNNER, "prospective_evidence_runner")
        expected = review_result()
        text = json.dumps(expected)
        claude_stdout = json.dumps({"result": text})
        claude_structured_stdout = json.dumps({"structured_output": expected})
        codex_stdout = json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
        )
        for stdout, command in (
            (claude_stdout, ["claude", "--output-format", "json"]),
            (
                claude_structured_stdout,
                ["claude", "--output-format", "json", "--json-schema", "{}"],
            ),
            (codex_stdout, ["codex", "exec", "--json"]),
        ):
            with self.subTest(command=command[0]):
                result_text, parsed = runner.extract_evidence_result(stdout, command, "review")
                self.assertEqual(json.loads(result_text), expected)
                self.assertEqual(parsed, expected)

        shapes = (
            ("", "empty"),
            (claude_structured_stdout, "structured_output"),
            (claude_stdout, "json_text"),
            (json.dumps({"result": f"```json\n{text}\n```"}), "fenced_json"),
            (json.dumps({"result": "plain planning prose"}), "prose"),
            (json.dumps({"usage": {"input_tokens": 1}}), "envelope_without_result"),
            ("not-json", "malformed_envelope"),
        )
        for stdout, expected_shape in shapes:
            with self.subTest(expected_shape=expected_shape):
                self.assertEqual(
                    runner.evidence_result_shape(stdout, ["claude", "--output-format", "json"]),
                    expected_shape,
                )

        non_finite = json.dumps(
            {"structured_output": {"schema_version": float("nan")}}, allow_nan=True
        )
        with self.assertRaises(runner.EvidenceResultError):
            runner.extract_evidence_result(
                non_finite,
                ["claude", "--output-format", "json", "--json-schema", "{}"],
                "review",
            )

    def test_sealed_workflow_runs_and_reports_without_cross_mode_reuse(self) -> None:
        campaign_module = load_module(CAMPAIGN, "prospective_evidence_campaign_run")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.repository(root)
            task = root / "task.txt"
            task.write_text("PRIVATE-TASK-MATERIAL", encoding="utf-8")
            task.chmod(0o600)
            out = root / "out"
            evaluator = self.evaluator(root)
            executor = self.executor(review_result())
            advisor = shlex.join(
                [sys.executable, "-c", "import sys;sys.stdin.read();print('unused')"]
            )
            manifest = campaign_module.build_manifest(
                arm="shape_b",
                planned_tasks=12,
                max_tasks=12,
                cost_basis="vendor",
                cheap=shlex.split(executor),
                expensive=shlex.split(executor),
                advisor=shlex.split(advisor),
                advisor_context="prompt",
                verify=evaluator,
                prices=None,
                workflow="review",
            )
            campaign_path = root / "campaign.json"
            campaign_module.write_manifest(campaign_path, manifest)
            command = [
                sys.executable,
                str(RUNNER),
                "--workflow",
                "review",
                "--repo",
                str(repo),
                "--task-file",
                str(task),
                "--cheap",
                executor,
                "--expensive",
                executor,
                "--advisor",
                advisor,
                "--advise-on-failure",
                "--confirm-task-egress",
                "--verify",
                str(evaluator),
                "--campaign",
                str(campaign_path),
                "--sample-ordinal",
                "1",
                "--out-dir",
                str(out),
            ]
            completed = subprocess.run(command, capture_output=True, check=False, text=True)
            report = subprocess.run(
                [
                    sys.executable,
                    str(REPORT),
                    "--log",
                    str(out / "runs.jsonl"),
                    "--campaign",
                    str(campaign_path),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            task.unlink()
            wrong_mode = list(command)
            wrong_mode[wrong_mode.index("review")] = "research"
            mismatched = subprocess.run(wrong_mode, capture_output=True, check=False, text=True)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("workflow=review", report.stdout)
        self.assertIn("planned_tasks_not_reached", report.stdout)
        self.assertEqual(mismatched.returncode, 2)
        self.assertIn("campaign contract mismatch", mismatched.stderr)
        self.assertNotIn("task.txt", mismatched.stderr)


if __name__ == "__main__":
    unittest.main()
