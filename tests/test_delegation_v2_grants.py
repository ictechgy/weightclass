import copy
import unittest

from tests.test_delegation_v2_compile import compilable_inputs, compile_raw
from weightclass.delegation_v2_schema import DelegationV2InvalidInputError


class DelegationV2GrantTests(unittest.TestCase):
    def test_one_directional_grant_may_serve_repeated_transition(self) -> None:
        policy, manifest = compilable_inputs()
        workflow = policy["workflows"][0]
        second = copy.deepcopy(workflow["tasks"][0])
        second["id"] = "other"
        second["requested_task_id"] = "requested-other"
        second["requested_dispatch_id"] = "dispatch-other"
        workflow["tasks"].append(second)
        compile_raw(policy, manifest)

    def test_rejects_missing_reverse_only_ambiguous_redundant_equal_and_unused_grants(self) -> None:
        for case in ("missing", "reverse", "ambiguous", "redundant", "equal", "unused"):
            policy, manifest = compilable_inputs()
            grants = policy["workflows"][0]["grants"]["provider"]
            if case == "missing":
                grants.clear()
            elif case == "reverse":
                grants[0]["from"], grants[0]["to"] = grants[0]["to"], grants[0]["from"]
            elif case == "ambiguous":
                grants.append({"id": "other", "from": "openai", "to": "anthropic"})
            elif case == "redundant":
                grants.append({"id": "other", "from": "anthropic", "to": "openai"})
            elif case == "equal":
                grants[0]["to"] = grants[0]["from"]
            else:
                grants.append({"id": "other", "from": "x", "to": "y"})
            with self.subTest(case=case), self.assertRaises(DelegationV2InvalidInputError):
                compile_raw(policy, manifest)


if __name__ == "__main__":
    unittest.main()
