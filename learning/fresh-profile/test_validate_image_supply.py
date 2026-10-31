#!/usr/bin/env python3
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import json

import yaml


MODULE = Path(__file__).with_name("validate_image_supply.py")
SPEC = importlib.util.spec_from_file_location("validate_image_supply", MODULE)
contract = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(contract)


class ImageSupplyTests(unittest.TestCase):
    def setUp(self):
        self.lock = contract.load_yaml(contract.LOCK)
        self.profile = contract.load_yaml(contract.HERE / "profile.template.yaml")
        self.profile_images = {item["name"]: item for item in self.profile["spec"]["images"]}

    def validate_copy(self, lock, require=False, profile=None, require_source=False):
        with tempfile.TemporaryDirectory(dir=contract.HERE) as directory:
            path = Path(directory) / "supply.yaml"
            profile_path = Path(directory) / "profile.yaml"
            path.write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")
            value = copy.deepcopy(profile or self.profile)
            if lock["spec"]["decision"]["status"] == "approved" and profile is None:
                decision = lock["spec"]["decision"]
                mode = decision["selectedMode"]
                value["spec"]["decisions"]["G2"] = {"status": "approved", "choice": mode}
                value["spec"]["imageSupply"].update(
                    decision=mode, immutable=True,
                    retentionUntil=decision["retentionUntil"],
                    restoreOwner=decision["restoreOwner"],
                )
            profile_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
            return contract.validate(path, require_fresh_evidence=require,
                                     profile_override=profile_path,
                                     require_source_evidence=require_source)

    def approve(self, mode="private-registry"):
        value = copy.deepcopy(self.lock)
        next(item for item in self.profile["spec"]["images"] if item["name"] == "account-runtime")["ref"] = (
            "ghcr.io/syuhei0519/backend-app@sha256:" + "b" * 64)
        value["spec"]["decision"] = {
            "status": "approved", "selectedMode": mode,
            "restoreOwner": "fixture-owner",
            "retentionUntil": "2099-10-25",
            "restoreProcedureRef": "runbook:restore-fresh-image-supply",
        }
        for item in value["spec"]["images"]:
            expected = contract.image_digest(self.profile_images[item["name"]]["ref"])
            manifest = {
                "kind": "single-platform", "manifestDigest": expected,
                "os": "linux", "architecture": "amd64",
            }
            selected_source = ("retained-oci-archive" if item["name"] == "account-runtime" else "public-registry") \
                if mode == "public-upstream-plus-retained-account-archive" else \
                "public-registry" if mode == "public-ghcr" else mode
            if selected_source in {"private-registry", "public-registry"}:
                item["source"] = {
                    "kind": selected_source, "published": True, "immutable": True,
                    "freshEligible": True,
                    "location": "oci://" + self.profile_images[item["name"]]["ref"],
                    "manifest": manifest,
                }
            else:
                item["source"] = {
                    "kind": selected_source, "published": False, "immutable": True,
                    "freshEligible": True,
                    "location": "archive-ref:fresh/" + item["name"] + ".oci.tar",
                    "archiveSha256": "sha256:" + "a" * 64,
                    "manifest": manifest,
                }
            item["retrieval"] = {
                "status": "succeeded", "emptyNodeCache": True,
                "observedDigest": expected, "observedArchitecture": "linux/amd64",
            }
            item["checks"] = {
                check: {"status": "passed", "evidenceRef": f"evidence:{item['name']}:{check}"}
                for check in contract.REQUIRED_CHECKS - {"migration", "smoke"}
            }
            item["checks"]["smoke"] = {
                "applicability": "required", "rawResult": "passed",
                "evidenceRef": f"evidence:{item['name']}:smoke",
                "evidenceScope": "selected-architecture-isolated-image",
            }
            item["checks"]["migration"] = (
                {"applicability": "required", "rawResult": "passed",
                 "evidenceRef": "evidence:account-runtime:migration",
                 "evidenceScope": "selected-architecture-isolated-image",
                 "reason": contract.MIGRATION_REQUIRED_REASON}
                if item["name"] == contract.MIGRATION_REQUIRED_IMAGE
                else copy.deepcopy(contract.MIGRATION_NOT_APPLICABLE))
        return value

    def test_public_template_contains_no_runtime_supply_evidence(self):
        lock, _ = contract.validate()
        self.assertEqual(len(lock["spec"]["images"]), 11)
        account = next(item for item in lock["spec"]["images"] if item["name"] == "account-runtime")
        self.assertEqual(account["source"]["kind"], "pending-g2")
        self.assertFalse(account["source"]["published"])
        self.assertFalse(account["source"]["freshEligible"])
        with self.assertRaisesRegex(contract.SupplyError, "approval-g2"):
            contract.validate(require_fresh_evidence=True)

    def test_account_artifact_is_not_rollouts_and_comparison_is_not_supply(self):
        value = copy.deepcopy(self.lock)
        account = next(item for item in value["spec"]["images"] if item["name"] == "account-runtime")
        account["source"] = {"kind": "checked-local-artifact", "published": False, "immutable": True,
            "freshEligible": False, "location": "checked-artifact:account-runtime", "retentionHours": 0,
            "sourceCommit": "1" * 40,
            "manifestDigest": "sha256:" + "2" * 64,
            "configDigest": "sha256:" + "3" * 64,
            "archiveSha256": "sha256:" + "4" * 64,
            "ciRunId": 1, "artifactId": 1,
            "entrypoint": ["/bin/rollouts-controller"], "user": "10001:10001"}
        with self.assertRaisesRegex(contract.SupplyError,
                                    "schema|account-artifact-profile-digest"):
            self.validate_copy(value)

    def test_registry_and_retained_archive_are_finite_approved_modes(self):
        for mode in ("private-registry", "retained-oci-archive",
                     "public-upstream-plus-retained-account-archive"):
            with self.subTest(mode=mode):
                self.validate_copy(self.approve(mode), require=True)

    def test_public_ghcr_requires_every_profile_image_delivery(self):
        value = self.approve("public-ghcr")
        with self.assertRaisesRegex(contract.SupplyError,
                                    "public-ghcr-undelivered:kind-node"):
            self.validate_copy(value)

    def test_public_ghcr_complete_delivery_fixture_has_no_archive(self):
        value = self.approve("public-ghcr")
        profile = copy.deepcopy(self.profile)
        profile["spec"]["decisions"]["G2"] = {
            "status": "approved", "choice": "public-ghcr"}
        profile["spec"]["imageSupply"].update(
            decision="public-ghcr", immutable=True,
            retentionUntil="2099-10-25", restoreOwner="fixture-owner")
        inventory = json.loads(contract.DELIVERY_INVENTORY.read_text())
        by_name = {item["name"]: item for item in inventory["images"]}
        aliases = {"postgres": "postgres-18"}
        profile_images = {item["name"]: item for item in profile["spec"]["images"]}
        account_ref = json.loads((contract.HERE / "security/canonical-contract.json").read_text())[
            "approvedRuntimeImage"]
        profile_images["account-runtime"]["ref"] = account_ref
        for name, profile_image in profile_images.items():
            if name == "account-runtime":
                continue
            inventory_name = aliases.get(name, name)
            image = by_name[inventory_name]
            digest = image["source"].rpartition("@")[2]
            inventory["deliveries"][inventory_name] = {
                "runUrl": "https://github.com/example/fixture/actions/runs/1",
                "destinationDigest": digest,
            }
            profile_image["ref"] = ("ghcr.io/syuhei0519/platform-lab-images:"
                                    + image["destinationTag"] + "@" + digest)
        sources = {item["name"]: item for item in value["spec"]["images"]}
        for name, profile_image in profile_images.items():
            sources[name]["source"]["location"] = "oci://" + profile_image["ref"]
            digest = contract.image_digest(profile_image["ref"])
            sources[name]["source"]["manifest"]["manifestDigest"] = digest
            sources[name]["retrieval"]["observedDigest"] = digest
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                    contract, "_validated_delivery_inventory", return_value=inventory):
                self.validate_copy(value, require=True, profile=profile)
        self.assertTrue(all(item["source"]["kind"] == "public-registry"
                            for item in value["spec"]["images"]))

    def test_precluster_source_provenance_does_not_require_postcluster_retrieval(self):
        value = self.approve("retained-oci-archive")
        for item in value["spec"]["images"]:
            item["retrieval"] = {"status": "pending", "emptyNodeCache": False,
                                 "observedDigest": "PENDING_POST_CLUSTER",
                                 "observedArchitecture": "PENDING_POST_CLUSTER"}
        self.validate_copy(value, require_source=True)
        with self.assertRaisesRegex(contract.SupplyError, "fresh-retrieval"):
            self.validate_copy(value, require=True)

    def test_supply_approval_must_match_profile_g2(self):
        value = self.approve()
        with self.assertRaisesRegex(contract.SupplyError, "profile-g2-approved"):
            self.validate_copy(value, require=True, profile=self.profile)

    def test_checked_artifact_and_node_cache_cannot_unlock_fresh(self):
        value = self.approve()
        account = next(item for item in value["spec"]["images"] if item["name"] == "account-runtime")
        account["source"] = {"kind": "pending-g2", "published": False, "immutable": False,
                             "freshEligible": False, "location": "PENDING_G2"}
        with self.assertRaisesRegex(contract.SupplyError, "approved-pending-source:account-runtime"):
            self.validate_copy(value, require=True)
        value = copy.deepcopy(self.lock)
        value["spec"]["prohibitedEvidence"].remove("old-node-cache")
        with self.assertRaisesRegex(contract.SupplyError, "prohibited-evidence"):
            self.validate_copy(value)

    def test_registry_unknown_and_image_absent_remain_distinct(self):
        for status, error in (("registry-unknown", "registry-unknown"), ("image-absent", "image-absent")):
            value = self.approve()
            value["spec"]["images"][0]["retrieval"]["status"] = status
            with self.subTest(status=status), self.assertRaisesRegex(contract.SupplyError, error):
                self.validate_copy(value, require=True)
        value = self.approve()
        value["spec"]["images"][0]["retrieval"]["status"] = "unknown"
        with self.assertRaisesRegex(contract.SupplyError, "schema"):
            self.validate_copy(value)

    def test_digest_architecture_and_all_runtime_checks_are_required(self):
        value = self.approve()
        value["spec"]["images"][0]["retrieval"]["observedArchitecture"] = "linux/arm64"
        with self.assertRaisesRegex(contract.SupplyError, "observed-architecture"):
            self.validate_copy(value, require=True)
        value = self.approve()
        value["spec"]["images"][0]["checks"]["entrypoint"]["status"] = "unrun"
        with self.assertRaisesRegex(contract.SupplyError, "check-entrypoint"):
            self.validate_copy(value, require=True)
        value = self.approve()
        value["spec"]["images"][0]["source"]["manifest"]["manifestDigest"] = \
            "sha256:" + "f" * 64
        with self.assertRaisesRegex(contract.SupplyError, "manifest-digest"):
            self.validate_copy(value, require=True)

    def test_migration_applicability_is_role_fixed(self):
        value = self.approve()
        account = next(item for item in value["spec"]["images"]
                       if item["name"] == "account-runtime")
        account["checks"]["migration"] = copy.deepcopy(
            contract.MIGRATION_NOT_APPLICABLE)
        with self.assertRaisesRegex(contract.SupplyError,
                                    "migration-required:account-runtime"):
            self.validate_copy(value)

        value = self.approve()
        postgres = next(item for item in value["spec"]["images"]
                        if item["name"] == "postgres")
        postgres["checks"]["migration"] = {
            "applicability": "required", "rawResult": "passed",
            "evidenceRef": "evidence:postgres:migration",
            "evidenceScope": "selected-architecture-isolated-image",
            "reason": contract.MIGRATION_REQUIRED_REASON,
        }
        with self.assertRaisesRegex(contract.SupplyError,
                                    "migration-not-applicable:postgres"):
            self.validate_copy(value)

    def test_failed_or_unrun_required_checks_never_satisfy_source_gate(self):
        for image_name, check_name in (("kind-node", "smoke"),
                                       ("account-runtime", "migration")):
            for raw_result in ("failed", "unrun"):
                value = self.approve()
                item = next(item for item in value["spec"]["images"]
                            if item["name"] == image_name)
                item["checks"][check_name]["rawResult"] = raw_result
                item["checks"][check_name]["evidenceRef"] = "evidence:real-but-not-passed"
                with self.subTest(image=image_name, check=check_name, raw=raw_result), \
                        self.assertRaisesRegex(contract.SupplyError,
                                               f"check-{check_name}:{image_name}"):
                    self.validate_copy(value, require_source=True)

        value = self.approve()
        value["spec"]["images"][0]["checks"]["scan"] = {
            "status": "failed", "evidenceRef": "external:bounded-lab-disposition"}
        with self.assertRaisesRegex(contract.SupplyError, "check-scan:kind-node"):
            self.validate_copy(value, require_source=True)

    def test_smoke_scope_cannot_be_live_integration(self):
        value = self.approve()
        value["spec"]["images"][0]["checks"]["smoke"]["evidenceScope"] = \
            "live-integration"
        with self.assertRaisesRegex(contract.SupplyError, "schema"):
            self.validate_copy(value)

    def test_multiarch_binds_index_child_scan_and_sbom(self):
        value = self.approve()
        item = value["spec"]["images"][0]
        index_digest = contract.image_digest(self.profile_images[item["name"]]["ref"])
        child_digest = "sha256:" + "c" * 64
        item["source"]["manifest"] = {
            "kind": "multi-platform-index", "indexDigest": index_digest,
            "childDigest": child_digest, "os": "linux", "architecture": "amd64",
            "childScanRef": "evidence:kind-node:child-scan",
            "childSbomRef": "evidence:kind-node:child-sbom",
        }
        item["retrieval"]["observedDigest"] = child_digest
        self.validate_copy(value, require=True)
        changed = copy.deepcopy(value)
        changed["spec"]["images"][0]["source"]["manifest"]["childSbomRef"] = "evidence:kind-node:child-scan"
        with self.assertRaisesRegex(contract.SupplyError, "index-child-evidence"):
            self.validate_copy(changed, require=True)

    def test_retention_must_be_future_and_owned(self):
        value = self.approve()
        value["spec"]["decision"]["retentionUntil"] = "2000-01-01"
        with self.assertRaisesRegex(contract.SupplyError, "retention-expired"):
            self.validate_copy(value, require=True)
        value = self.approve()
        value["spec"]["decision"]["restoreOwner"] = "PENDING_G2"
        with self.assertRaisesRegex(contract.SupplyError, "approved-placeholder"):
            self.validate_copy(value, require=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
