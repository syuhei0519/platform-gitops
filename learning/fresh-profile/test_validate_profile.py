#!/usr/bin/env python3
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

import yaml


MODULE = Path(__file__).with_name("validate_profile.py")
SPEC = importlib.util.spec_from_file_location("validate_profile", MODULE)
contract = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(contract)


class FreshProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = contract.load_yaml(contract.PROFILE)

    def validate_copy(self, profile, require_approved=False):
        # Keep mutations inside the checkout so restricted runners never fall
        # back to a host temp directory they cannot write.
        with tempfile.TemporaryDirectory(dir=contract.HERE) as directory:
            path = Path(directory) / "profile.yaml"
            path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
            return contract.validate(path, require_approved=require_approved)

    def test_offline_profile_is_complete_but_not_live_approved(self):
        profile, dag, equivalence = contract.validate()
        self.assertEqual(len(profile["spec"]["repositories"]), 4)
        self.assertEqual(len(profile["spec"]["images"]), 11)
        self.assertEqual(len(dag["spec"]["steps"]), 15)
        self.assertEqual(len(equivalence["mappings"]), 8)
        with self.assertRaisesRegex(contract.ContractError, "approval-g1"):
            contract.validate(require_approved=True)

    def test_variable_revision_is_rejected(self):
        value = copy.deepcopy(self.profile)
        value["spec"]["repositories"]["platform-gitops"]["commit"] = "main"
        with self.assertRaisesRegex(contract.ContractError, "schema"):
            self.validate_copy(value)

    def test_current_cluster_is_rejected(self):
        value = copy.deepcopy(self.profile)
        value["spec"]["target"]["clusterName"] = contract.CURRENT_CLUSTER
        with self.assertRaisesRegex(contract.ContractError, "current-cluster-target"):
            self.validate_copy(value)

    def test_image_digest_architecture_and_identity_are_finite(self):
        for mutation, error in (
            (lambda v: v["spec"]["images"][0].update(ref="kindest/node:latest"), "schema"),
            (lambda v: v["spec"]["images"][0].update(architecture="linux/arm64"), "schema"),
            (lambda v: v["spec"]["images"][0].update(name=v["spec"]["images"][1]["name"]), "image-set"),
        ):
            value = copy.deepcopy(self.profile)
            mutation(value)
            with self.assertRaisesRegex(contract.ContractError, error):
                self.validate_copy(value)

    def test_node_cache_cannot_be_supply_evidence(self):
        value = copy.deepcopy(self.profile)
        value["spec"]["imageSupply"]["cacheIsEvidence"] = True
        with self.assertRaisesRegex(contract.ContractError, "schema"):
            self.validate_copy(value)

    def test_secret_value_fields_are_rejected(self):
        value = copy.deepcopy(self.profile)
        value["spec"]["inputs"]["secrets"][0]["password"] = "synthetic"
        with self.assertRaisesRegex(contract.ContractError, "schema"):
            self.validate_copy(value)
        value = copy.deepcopy(self.profile)
        value["spec"]["inputs"]["synthetic"] = {"token": "synthetic"}
        with self.assertRaisesRegex(contract.ContractError, "schema"):
            self.validate_copy(value)

    def test_budget_and_stop_conditions_are_bounded(self):
        value = copy.deepcopy(self.profile)
        value["spec"]["budget"]["approvalLimits"]["elapsedMaxMinutes"] = 600
        with self.assertRaisesRegex(contract.ContractError, "pending-g1-budget"):
            self.validate_copy(value)
        value = copy.deepcopy(self.profile)
        value["spec"]["budget"]["stopOn"][-1] = "continue-after-unexpected-restart"
        with self.assertRaisesRegex(contract.ContractError, "stop-budget"):
            self.validate_copy(value)

    def test_dag_order_dependency_and_resume_are_strict(self):
        dag = contract.load_yaml(contract.HERE / "bootstrap-dag.yaml")
        changed = copy.deepcopy(dag)
        changed["spec"]["steps"][4]["requires"] = ["cluster"]
        with self.assertRaisesRegex(contract.ContractError, "dag-dependency"):
            contract.validate_dag(changed)
        changed = copy.deepcopy(dag)
        changed["spec"]["checkpoint"]["resume"]["neverRecreateSucceededMigrationJob"] = False
        with self.assertRaisesRegex(contract.ContractError, "checkpoint-resume"):
            contract.validate_dag(changed)

    def test_prior_done_evidence_cannot_be_omitted(self):
        value = contract.json.loads((contract.HERE / "equivalence.json").read_text(encoding="utf-8"))
        value["mappings"].pop()
        with self.assertRaisesRegex(contract.ContractError, "equivalence-tickets"):
            contract.validate_equivalence(value)

    def test_explicit_g1_g2_g3_can_unlock_live_validation(self):
        value = copy.deepcopy(self.profile)
        value["spec"]["freshRuntimeRequirement"]["admissionSecretKeyContract"] = \
            "username-password-operation-key-allowlist-aligned"
        value["spec"]["target"].update(clusterName="kind-platform-fresh-plat217", context="kind-platform-fresh-plat217")
        for gate in ("G1", "G2", "G3"):
            value["spec"]["decisions"][gate] = {"status": "approved", "choice": "approved-fixture-choice"}
        value["spec"]["imageSupply"].update(
            decision="retained-oci-archive", immutable=True,
            retentionUntil="2027-10-25", restoreOwner="personal-platform-owner")
        for item in value["spec"]["inputs"]["secrets"]:
            item["owner"] = "personal-platform-owner"
            item["expiry"] = "2026-11-25"
            item["sourceRef"] = "sealed-input:sha256:" + "3" * 64
        value["spec"]["repositories"]["platform-gitops"]["commit"] = "1" * 40
        next(item for item in value["spec"]["images"] if item["name"] == "account-runtime")["ref"] = (
            "ghcr.io/syuhei0519/backend-app@sha256:" + "2" * 64)
        limits = value["spec"]["budget"]["approvalLimits"]
        limits.update(elapsedMaxMinutes=45, hostAvailableMemoryMinGiB=4,
                      hostFreeDiskMinGiB=20, dockerAdditionalDiskMaxGiB=15,
                      workloadCPURequestsMaxMilli=2000, workloadMemoryRequestsMaxMiB=4096,
                      imageBytesMaxGiB=15, podPendingMaxSeconds=60)
        self.validate_copy(value, require_approved=True)

    def test_approved_profile_rejects_unverified_plat211_image(self):
        value = copy.deepcopy(self.profile)
        value["spec"]["freshRuntimeRequirement"]["admissionSecretKeyContract"] = \
            "username-password-operation-key-allowlist-aligned"
        value["spec"]["target"].update(clusterName="kind-platform-fresh-plat217", context="kind-platform-fresh-plat217")
        for gate in ("G1", "G2", "G3"):
            value["spec"]["decisions"][gate] = {"status": "approved", "choice": "approved-fixture-choice"}
        value["spec"]["imageSupply"].update(decision="retained-oci-archive", immutable=True,
                                             retentionUntil="2027-10-25", restoreOwner="personal-platform-owner")
        for item in value["spec"]["inputs"]["secrets"]:
            item.update(owner="personal-platform-owner", expiry="2026-11-25",
                        sourceRef="sealed-input:sha256:" + "3" * 64)
        value["spec"]["repositories"]["platform-gitops"]["commit"] = "1" * 40
        value["spec"]["budget"]["approvalLimits"].update(
            elapsedMaxMinutes=45, hostAvailableMemoryMinGiB=4, hostFreeDiskMinGiB=20,
            dockerAdditionalDiskMaxGiB=15, workloadCPURequestsMaxMilli=2000,
            workloadMemoryRequestsMaxMiB=4096, imageBytesMaxGiB=15, podPendingMaxSeconds=60)
        with self.assertRaisesRegex(contract.ContractError, "approved-account-runtime-pending"):
            self.validate_copy(value, require_approved=True)

    def test_approved_profile_rejects_pending_admission_secret_key_contract(self):
        value = copy.deepcopy(self.profile)
        value["spec"]["target"].update(clusterName="kind-platform-fresh-plat217",
                                        context="kind-platform-fresh-plat217")
        for gate in ("G1", "G2", "G3"):
            value["spec"]["decisions"][gate] = {"status": "approved", "choice": "approved-fixture-choice"}
        value["spec"]["imageSupply"].update(
            decision="retained-oci-archive", immutable=True,
            retentionUntil="2027-10-25", restoreOwner="personal-platform-owner")
        for item in value["spec"]["inputs"]["secrets"]:
            item.update(owner="personal-platform-owner", expiry="2026-11-25",
                        sourceRef="sealed-input:sha256:" + "3" * 64)
        value["spec"]["repositories"]["platform-gitops"]["commit"] = "1" * 40
        next(item for item in value["spec"]["images"] if item["name"] == "account-runtime")["ref"] = (
            "ghcr.io/syuhei0519/backend-app@sha256:" + "2" * 64)
        value["spec"]["budget"]["approvalLimits"].update(
            elapsedMaxMinutes=45, hostAvailableMemoryMinGiB=4, hostFreeDiskMinGiB=20,
            dockerAdditionalDiskMaxGiB=15, workloadCPURequestsMaxMilli=2000,
            workloadMemoryRequestsMaxMiB=4096, imageBytesMaxGiB=15, podPendingMaxSeconds=60)
        with self.assertRaisesRegex(contract.ContractError, "approved-admission-secret-key-contract"):
            self.validate_copy(value, require_approved=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
