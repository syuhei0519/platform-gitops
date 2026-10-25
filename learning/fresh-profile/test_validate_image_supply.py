#!/usr/bin/env python3
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

import yaml


MODULE = Path(__file__).with_name("validate_image_supply.py")
SPEC = importlib.util.spec_from_file_location("validate_image_supply", MODULE)
contract = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(contract)


class ImageSupplyTests(unittest.TestCase):
    def setUp(self):
        self.lock = contract.load_yaml(contract.LOCK)
        self.profile = contract.load_yaml(contract.HERE / "profile.lock.yaml")
        self.profile_images = {item["name"]: item for item in self.profile["spec"]["images"]}

    def validate_copy(self, lock, require=False, profile=None):
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
                                     profile_override=profile_path)

    def approve(self, mode="private-registry"):
        value = copy.deepcopy(self.lock)
        value["spec"]["decision"] = {
            "status": "approved", "selectedMode": mode,
            "restoreOwner": "personal-platform-owner",
            "retentionUntil": "2099-10-25",
            "restoreProcedureRef": "runbook:restore-fresh-image-supply",
        }
        for item in value["spec"]["images"]:
            expected = contract.image_digest(self.profile_images[item["name"]]["ref"])
            manifest = {
                "kind": "single-platform", "manifestDigest": expected,
                "os": "linux", "architecture": "amd64",
            }
            if mode == "private-registry":
                item["source"] = {
                    "kind": mode, "published": True, "immutable": True,
                    "freshEligible": True,
                    "location": "oci://" + self.profile_images[item["name"]]["ref"],
                    "manifest": manifest,
                }
            else:
                item["source"] = {
                    "kind": mode, "published": False, "immutable": True,
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
                for check in contract.REQUIRED_CHECKS
            }
        return value

    def test_pending_lock_is_complete_but_not_fresh_evidence(self):
        lock, _ = contract.validate()
        self.assertEqual(len(lock["spec"]["images"]), 11)
        account = next(item for item in lock["spec"]["images"] if item["name"] == "account-runtime")
        self.assertEqual(account["source"]["kind"], "checked-local-artifact")
        self.assertFalse(account["source"]["published"])
        self.assertFalse(account["source"]["freshEligible"])
        self.assertEqual(account["source"]["entrypoint"], ["/server"])
        self.assertEqual(account["source"]["user"], "10001:10001")
        with self.assertRaisesRegex(contract.SupplyError, "approval-g2"):
            contract.validate(require_fresh_evidence=True)

    def test_account_artifact_is_not_rollouts_and_comparison_is_not_supply(self):
        value = copy.deepcopy(self.lock)
        account = next(item for item in value["spec"]["images"] if item["name"] == "account-runtime")
        account["source"]["manifestDigest"] = "sha256:966adba896524519bd1653b31ade97b624dd5cbf83623f7cdeb5c5708aaa69e0"
        account["source"]["entrypoint"] = ["/bin/rollouts-controller"]
        with self.assertRaisesRegex(contract.SupplyError, "schema|account-artifact-identity"):
            self.validate_copy(value)

    def test_registry_and_retained_archive_are_finite_approved_modes(self):
        for mode in ("private-registry", "retained-oci-archive"):
            with self.subTest(mode=mode):
                self.validate_copy(self.approve(mode), require=True)

    def test_supply_approval_must_match_profile_g2(self):
        value = self.approve()
        with self.assertRaisesRegex(contract.SupplyError, "profile-g2-approved"):
            self.validate_copy(value, require=True, profile=self.profile)

    def test_checked_artifact_and_node_cache_cannot_unlock_fresh(self):
        value = self.approve()
        account = next(item for item in value["spec"]["images"] if item["name"] == "account-runtime")
        account["source"] = copy.deepcopy(next(
            item["source"] for item in self.lock["spec"]["images"] if item["name"] == "account-runtime"
        ))
        with self.assertRaisesRegex(contract.SupplyError, "approved-source-mode:account-runtime"):
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
        value["spec"]["images"][0]["source"]["manifest"]["manifestDigest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(contract.SupplyError, "manifest-digest"):
            self.validate_copy(value, require=True)

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
