#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import unittest

import yaml


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
PLACEHOLDER_PREFIX = "<runtime-input:"


def load_yaml(name):
    return yaml.safe_load((HERE / name).read_text(encoding="utf-8"))


class RuntimeEvidenceSeparationTests(unittest.TestCase):
    def test_runtime_paths_are_ignored_and_untracked(self):
        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPOSITORY, check=True,
            capture_output=True).stdout.decode().split("\0")
        self.assertFalse(any("learning/fresh-profile/.runtime/" in item for item in tracked))
        self.assertFalse(any(".runtime.local." in item for item in tracked))
        for name in ("profile.runtime.local.yaml", "image-supply.runtime.local.yaml",
                     "acceptance.runtime.local.json", "approval.runtime.local.json"):
            candidate = HERE / ".runtime" / name
            result = subprocess.run(
                ["git", "check-ignore", "--no-index", "--quiet", str(candidate)],
                cwd=REPOSITORY, check=False)
            self.assertEqual(result.returncode, 0, name)

    def test_public_profile_contains_placeholders_not_host_or_credential_values(self):
        profile = load_yaml("profile.template.yaml")
        spec = profile["spec"]
        self.assertTrue(spec["scope"]["currentClusterExcluded"].startswith(PLACEHOLDER_PREFIX))
        self.assertTrue(spec["target"]["clusterName"].startswith(PLACEHOLDER_PREFIX))
        self.assertTrue(spec["target"]["context"].startswith(PLACEHOLDER_PREFIX))
        observation = spec["budget"]["hostObservation"]
        self.assertTrue(all(isinstance(value, str) and value.startswith(PLACEHOLDER_PREFIX)
                            for value in observation.values()))
        plan = spec["inputs"]["credentialIssuancePlan"]
        for field in ("planId", "procedureRef", "procedureSha256"):
            self.assertTrue(plan[field].startswith(PLACEHOLDER_PREFIX), (field, plan[field]))
        for item in [*plan["secrets"], plan["ca"]]:
            for field in ("owner", "expiry", "issuerRef", "cleanupOwner"):
                self.assertTrue(item[field].startswith(PLACEHOLDER_PREFIX), (field, item[field]))
            self.assertNotIn("sourceRef", item)
            self.assertNotIn("uid", item)

    def test_public_supply_and_records_contain_no_runtime_instance(self):
        supply = load_yaml("image-supply.template.yaml")
        self.assertEqual(supply["spec"]["decision"]["status"], "pending-approval")
        for item in supply["spec"]["images"]:
            self.assertEqual(item["source"]["kind"], "pending-g2")
            self.assertNotIn("comparisonOnly", item)
        acceptance = json.loads((HERE / "acceptance.template.json").read_text(encoding="utf-8"))
        approval = json.loads((HERE / "approval.template.json").read_text(encoding="utf-8"))
        for record in (acceptance["target"], approval["spec"]["target"]):
            self.assertTrue(record["clusterName"].startswith(PLACEHOLDER_PREFIX))
            self.assertTrue(record["context"].startswith(PLACEHOLDER_PREFIX))

    def test_public_contract_is_only_reproducible_bootstrap_and_safety_data(self):
        contract = load_yaml("bootstrap.contract.yaml")["spec"]
        self.assertEqual(set(contract), {"runtimeEvidence", "platform", "rollouts", "safetyLimits"})
        self.assertEqual(contract["rollouts"]["chartVersion"], "2.43.7")
        self.assertEqual(contract["rollouts"]["crdCount"], 5)
        self.assertFalse(contract["rollouts"]["controllerInstallCRDs"])
        self.assertTrue(contract["runtimeEvidence"]["mustBeGitIgnored"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
