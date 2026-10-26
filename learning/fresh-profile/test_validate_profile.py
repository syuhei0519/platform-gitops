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

    def approved_profile(self):
        value = copy.deepcopy(self.profile)
        value["metadata"]["purpose"] = "runtime-evidence"
        value["spec"]["scope"]["currentClusterExcluded"] = "kind-protected-existing"
        value["spec"]["freshRuntimeRequirement"]["admissionSecretKeyContract"] = \
            "username-password-operation-key-allowlist-aligned"
        value["spec"]["target"].update(
            clusterName="kind-platform-fresh-fixture", context="kind-platform-fresh-fixture")
        for gate in ("G1", "G2", "G3"):
            value["spec"]["decisions"][gate] = {
                "status": "approved", "choice": "approved-fixture-choice"}
        value["spec"]["imageSupply"].update(
            decision="retained-oci-archive", immutable=True,
            retentionUntil="2027-10-25", restoreOwner="fixture-owner")
        secret_contract = (
            ("account-db-bootstrap", "fresh-account/account-db-bootstrap"),
            ("account-db-runtime", "fresh-account/account-db-runtime"),
            ("account-db-migration", "fresh-account/account-db-migration"),
        )
        for item, (name, destination) in zip(
                value["spec"]["inputs"]["secrets"], secret_contract):
            item.update(name=name, destination=destination, owner="fixture-owner",
                        expiry="2026-11-25",
                        sourceRef="sealed-input:sha256:" + "3" * 64)
        value["spec"]["repositories"]["platform-gitops"]["commit"] = "1" * 40
        next(item for item in value["spec"]["images"]
             if item["name"] == "account-runtime")["ref"] = (
                 "ghcr.io/syuhei0519/backend-app@sha256:" + "2" * 64)
        value["spec"]["budget"]["hostObservation"] = {
            "capturedAt": "2026-10-26T00:00:00Z", "logicalCpu": 8,
            "installedMemoryGiB": 16, "availableMemoryGiB": 9,
            "freeDiskGiB": 401, "dockerCpuLimit": 8,
            "dockerMemoryLimitGiB": 12, "dockerImagesGiB": 1,
            "dockerVolumesGiB": 1, "dockerBuildCacheGiB": 1,
            "existingRunningContainers": 0,
        }
        value["spec"]["budget"]["approvalLimits"].update(
            workloadCPURequestsMaxMilli=2000, workloadMemoryRequestsMaxMiB=4096,
            imageBytesMaxGiB=15)
        value["spec"]["contracts"]["imageSupply"] = "image-supply.lock.yaml"
        return value

    def test_offline_profile_is_complete_but_not_live_approved(self):
        profile, dag, equivalence = contract.validate()
        self.assertEqual(len(profile["spec"]["repositories"]), 4)
        self.assertEqual(len(profile["spec"]["images"]), 11)
        self.assertEqual(len(dag["spec"]["steps"]), 15)
        self.assertEqual(len(equivalence["mappings"]), 8)
        with self.assertRaisesRegex(contract.ContractError, "runtime-evidence-placeholder"):
            contract.validate(require_approved=True)

    def test_variable_revision_is_rejected(self):
        value = copy.deepcopy(self.profile)
        value["spec"]["repositories"]["platform-gitops"]["commit"] = "main"
        with self.assertRaisesRegex(contract.ContractError, "schema"):
            self.validate_copy(value)

    def test_current_cluster_is_rejected(self):
        value = copy.deepcopy(self.profile)
        value["spec"]["scope"]["currentClusterExcluded"] = "kind-protected-existing"
        value["spec"]["target"]["clusterName"] = "kind-protected-existing"
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

    def test_multiarch_children_use_digest_only_references(self):
        images = {item["name"]: item["ref"] for item in self.profile["spec"]["images"]}
        for name in contract.DIGEST_ONLY_IMAGES:
            repository = images[name].split("@", 1)[0]
            self.assertNotIn(":", repository.rsplit("/", 1)[-1])

        value = copy.deepcopy(self.profile)
        argocd = next(item for item in value["spec"]["images"] if item["name"] == "argocd")
        argocd["ref"] = argocd["ref"].replace("argocd@", "argocd:v3.5.2@")
        with self.assertRaisesRegex(contract.ContractError, "version-lock-image"):
            self.validate_copy(value)

    def test_rollouts_chart_version_and_digest_are_version_lock_bound(self):
        public = contract.public_contract()["spec"]["rollouts"]
        self.assertEqual(public["chartVersion"], "2.43.7")
        self.assertEqual(len(public["packageSha256"]), 64)
        self.assertEqual(len(public["crdListSha256"]), 64)
        value = copy.deepcopy(self.profile)
        value["spec"]["tools"]["rollouts"] = "v1.10.0"
        with self.assertRaisesRegex(contract.ContractError, "version-lock-tool"):
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
        with self.assertRaisesRegex(contract.ContractError, "public-safety-limit"):
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
        value = self.approved_profile()
        self.validate_copy(value, require_approved=True)

    def test_approved_profile_rejects_unverified_plat211_image(self):
        value = self.approved_profile()
        next(item for item in value["spec"]["images"] if item["name"] == "account-runtime")["ref"] = \
            "PENDING_PLAT211_VERIFIED_IMAGE"
        with self.assertRaisesRegex(contract.ContractError, "approved-account-runtime-pending"):
            self.validate_copy(value, require_approved=True)

    def test_approved_profile_rejects_pending_admission_secret_key_contract(self):
        value = self.approved_profile()
        value["spec"]["freshRuntimeRequirement"]["admissionSecretKeyContract"] = \
            "PENDING_EXACT_ADMISSION_SECRET_KEY_APPROVAL"
        with self.assertRaisesRegex(contract.ContractError, "approved-admission-secret-key-contract"):
            self.validate_copy(value, require_approved=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
