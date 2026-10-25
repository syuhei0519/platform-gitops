#!/usr/bin/env python3
import copy
import importlib.util
from pathlib import Path
import unittest


MODULE = Path(__file__).with_name("validate_authority.py")
SPEC = importlib.util.spec_from_file_location("validate_authority", MODULE)
contract = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(contract)


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.matrix = contract.load(contract.MATRIX)
        self.evidence = contract.load(contract.EVIDENCE)

    def assertEvidenceRejected(self, reason):
        with self.assertRaisesRegex(contract.ContractError, reason):
            contract.validate_evidence(self.matrix, self.evidence)

    def test_canonical_two_sha_authority_is_valid_offline(self):
        matrix, evidence = contract.validate()
        self.assertEqual(set(matrix["sources"]), contract.ACTORS)
        self.assertEqual(len(evidence["applications"]), 2)
        self.assertEqual(matrix["liveEvidence"]["status"], "not-run")

    def test_variable_or_wrong_source_sha_is_rejected(self):
        self.evidence["sources"]["workload"]["commit"] = "main"
        self.assertEvidenceRejected("evidence-sha:workload")

    def test_arbitrary_child_application_is_rejected(self):
        child = copy.deepcopy(self.evidence["applications"][1])
        child["name"] = "unreviewed-child"
        self.evidence["applications"].append(child)
        self.assertEvidenceRejected("application-set")

    def test_unbounded_cluster_write_is_rejected(self):
        changed = copy.deepcopy(self.matrix)
        changed["permissions"][0]["resourceNames"] = []
        with self.assertRaisesRegex(contract.ContractError, "permission-resource-names"):
            contract.validate_matrix(changed)

    def test_privilege_escalation_verb_is_rejected(self):
        changed = copy.deepcopy(self.matrix)
        changed["permissions"][0]["verbs"].append("escalate")
        with self.assertRaisesRegex(contract.ContractError, "permission-verb"):
            contract.validate_matrix(changed)

    def test_controller_service_account_bypass_is_rejected(self):
        self.evidence["controllers"][0]["podSpec"]["serviceAccountName"] = "argocd-application-controller"
        self.assertEvidenceRejected("controller-service-account")

    def test_controller_automount_bypass_is_rejected(self):
        self.evidence["controllers"][0]["podSpec"]["automountServiceAccountToken"] = True
        self.assertEvidenceRejected("controller-automount")

    def test_controller_secret_mount_bypass_is_rejected(self):
        self.evidence["controllers"][0]["podSpec"]["volumes"].append({
            "name": "other", "secret": {"secretName": "repository-read"}
        })
        self.assertEvidenceRejected("controller-secret-keys")

    def test_privileged_controller_is_rejected(self):
        container = self.evidence["controllers"][0]["podSpec"]["containers"][0]
        container["securityContext"]["privileged"] = True
        self.assertEvidenceRejected("controller-privileged")

    def test_unknown_operation_name_is_rejected(self):
        self.evidence["operations"][3]["name"] = "arbitrary-deployment"
        self.assertEvidenceRejected("operation-authority")

    def test_pvc_secret_and_field_ownership_semantics_are_retained(self):
        self.evidence["retained"]["persistentVolumeClaims"]["fresh-account/account-data"]["storageClassName"] = "other"
        self.assertEvidenceRejected("retained-semantics")


if __name__ == "__main__":
    unittest.main(verbosity=2)
