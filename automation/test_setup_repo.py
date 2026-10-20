import copy
import unittest
from unittest.mock import patch

import setup_repo


class SetupPayloadTests(unittest.TestCase):
    def test_queue_payload_builds_the_largest_group(self):
        for stored in (False, True):
            for existing_queue in (False, True):
                with self.subTest(stored=stored, existing_queue=existing_queue):
                    ruleset = {
                        "id": 42,
                        "name": "dev",
                        "target": "branch",
                        "enforcement": "active",
                        "rules": [{"type": "deletion"}],
                    }
                    if existing_queue:
                        ruleset["rules"].append(
                            {
                                "type": "merge_queue",
                                "parameters": {
                                    "max_entries_to_build": 1,
                                    "max_entries_to_merge": 1,
                                    "check_response_timeout_minutes": 17,
                                    "merge_method": "REBASE",
                                },
                            }
                        )
                    original = copy.deepcopy(ruleset)
                    source = ("ruleset.json", ruleset) if stored else None
                    body = setup_repo.ruleset_body(ruleset, source)
                    with patch.object(
                        setup_repo, "gh", return_value={**body, "id": 42}
                    ) as api:
                        setup_repo.apply_ruleset("example", ruleset, body)
                    api.assert_called_once_with(
                        [
                            "repos/the-cloud-clockwork/example/rulesets/42",
                            "--method",
                            "PUT",
                        ],
                        body,
                    )
                    queues = [
                        rule for rule in body["rules"] if rule["type"] == "merge_queue"
                    ]
                    self.assertEqual(len(queues), 1)
                    self.assertEqual(
                        queues[0]["parameters"],
                        {
                            "grouping_strategy": "ALLGREEN",
                            "max_entries_to_build": 5,
                            "max_entries_to_merge": 5,
                            "min_entries_to_merge": 1,
                            "min_entries_to_merge_wait_minutes": 1,
                            "check_response_timeout_minutes": 17
                            if existing_queue
                            else 25,
                            "merge_method": "REBASE" if existing_queue else "SQUASH",
                        },
                    )
                    self.assertEqual(body["rules"][0], {"type": "deletion"})
                    self.assertEqual(ruleset, original)
