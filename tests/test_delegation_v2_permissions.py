import unittest

from tests.test_delegation_v2_graph import parsed_workflow, workflow_with_tasks
from weightclass.delegation_v2_graph import validate_delegation_v2_graph
from weightclass.delegation_v2_permissions import task_requests_write
from weightclass.delegation_v2_schema import DelegationV2InvalidInputError


class DelegationV2PermissionTests(unittest.TestCase):
    def test_closed_write_predicate_rows(self) -> None:
        cases = (
            ("none", "deny", "none", (), False),
            ("read", "deny", "read_only", (), False),
            ("write", "deny", "read_only", (), True),
            ("read", "allow", "read_only", (), True),
            ("read", "deny", "mutable", (), True),
            ("read", "deny", "read_only", ("workspace-write",), True),
            ("read", "deny", "read_only", ("workspace-read",), False),
        )
        for filesystem, commands, worktree, capabilities, expected in cases:
            raw, workflow = workflow_with_tasks("task")
            task = workflow["tasks"][0]
            task["request"]["permissions"] = {
                "filesystem": filesystem,
                "commands": commands,
            }
            task["worktree"] = {
                "mode": worktree,
                "scope": "workspace" if worktree != "none" else None,
            }
            task["capabilities"] = list(capabilities)
            self.assertIs(task_requests_write(parsed_workflow(raw).tasks[0]), expected)

    def test_equal_and_both_ancestor_overlap_directions_fail_for_writers(self) -> None:
        for left, right in (
            ("workspace", "workspace"),
            ("workspace", "workspace/sub"),
            ("workspace/sub", "workspace"),
        ):
            raw, workflow = workflow_with_tasks("a", "b")
            for task, scope in zip(workflow["tasks"], (left, right), strict=True):
                task["request"]["permissions"]["filesystem"] = "write"
                task["mutable_scopes"] = [scope]
            with (
                self.subTest(left=left, right=right),
                self.assertRaises(DelegationV2InvalidInputError),
            ):
                validate_delegation_v2_graph(parsed_workflow(raw))

    def test_siblings_and_nonwriters_do_not_conflict(self) -> None:
        raw, workflow = workflow_with_tasks("a", "b")
        workflow["tasks"][0]["mutable_scopes"] = ["workspace/a"]
        workflow["tasks"][1]["mutable_scopes"] = ["workspace/b"]
        for task in workflow["tasks"]:
            task["request"]["permissions"]["filesystem"] = "write"
        validate_delegation_v2_graph(parsed_workflow(raw))
        for task in workflow["tasks"]:
            task["request"]["permissions"]["filesystem"] = "read"
            task["mutable_scopes"] = ["workspace"]
        validate_delegation_v2_graph(parsed_workflow(raw))


if __name__ == "__main__":
    unittest.main()
