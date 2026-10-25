#!/usr/bin/env python3
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


MODULE = Path(__file__).with_name("validate_acceptance.py")
SPEC = importlib.util.spec_from_file_location("validate_acceptance", MODULE)
contract = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(contract)


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.pending = contract.load_json(contract.RECORD)
        self.profile = contract.profile_contract.load_yaml(contract.PROFILE)

    def files(self, record, profile=None):
        directory = tempfile.TemporaryDirectory(dir=contract.HERE)
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        profile_path = root / "profile.yaml"
        profile_path.write_text(yaml.safe_dump(profile or self.profile, sort_keys=False), encoding="utf-8")
        value = copy.deepcopy(record)
        value["profile"]["sha256"] = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        record_path = root / "record.json"
        record_path.write_text(json.dumps(value), encoding="utf-8")
        return record_path, profile_path, value

    def approved(self):
        profile = copy.deepcopy(self.profile)
        profile["spec"]["target"].update(
            clusterName="kind-platform-fresh-plat217",
            context="kind-platform-fresh-plat217",
        )
        for gate in ("G1", "G2", "G3"):
            profile["spec"]["decisions"][gate] = {"status": "approved", "choice": "approved-test-fixture"}
        profile["spec"]["imageSupply"].update(
            decision="retained-oci-archive", immutable=True,
            retentionUntil="2027-10-25", restoreOwner="personal-platform-owner",
        )
        for item in profile["spec"]["inputs"]["secrets"]:
            item["owner"] = "personal-platform-owner"
            item["expiry"] = "2026-11-25"
            item["sourceRef"] = "sealed-input:sha256:" + "3" * 64
        profile["spec"]["repositories"]["platform-gitops"]["commit"] = "1" * 40
        runtime = next(item for item in profile["spec"]["images"] if item["name"] == "account-runtime")
        runtime["ref"] = "ghcr.io/syuhei0519/backend-app@sha256:" + "3" * 64
        limits = profile["spec"]["budget"]["approvalLimits"]
        limits.update(elapsedMaxMinutes=45, hostAvailableMemoryMinGiB=4,
                      hostFreeDiskMinGiB=20, dockerAdditionalDiskMaxGiB=15,
                      workloadCPURequestsMaxMilli=2000, workloadMemoryRequestsMaxMiB=4096,
                      imageBytesMaxGiB=15, podPendingMaxSeconds=60)
        record = copy.deepcopy(self.pending)
        repositories = {name: value["commit"] for name, value in profile["spec"]["repositories"].items()}
        record["profile"]["repositories"] = copy.deepcopy(repositories)
        record["evidence"]["revisions"]["desired"] = copy.deepcopy(repositories)
        record["metadata"].update(recordedAt="2026-10-25T21:00:00Z", liveEvidence=True)
        record["target"].update(
            clusterName="kind-platform-fresh-plat217",
            context="kind-platform-fresh-plat217",
            clusterUID="fixture-cluster-uid",
        )
        for image in record["evidence"]["images"]:
            if image["name"] == "account-runtime":
                image["expectedDigest"] = "sha256:" + "3" * 64
            image.update(
                status="passed", retrievedDigest=image["expectedDigest"],
                actualArchitecture=image["expectedArchitecture"],
                sourceType="retained-oci-archive",
            )
        resources = record["evidence"]["resources"]
        resources.update(status="passed", observed=list(resources["expected"]))
        revisions = record["evidence"]["revisions"]
        revisions.update(status="passed", actual=copy.deepcopy(revisions["desired"]))
        for item in (record["evidence"]["readiness"] + record["evidence"]["resyncRestart"] +
                     record["evidence"]["progressiveDelivery"] + record["evidence"]["processCleanup"]):
            item.update(status="passed", detail="synthetic unit fixture")
        for item in record["evidence"]["databaseAuthorization"]:
            item.update(status="passed", observed=item["expected"], detail="synthetic isolated database fixture")
        bindings = record["evidence"]["temporaryBindings"]
        bindings.update(status="passed", observedRemoved=list(bindings["expectedRemoved"]),
                        detail="synthetic no-binding observation")
        ownership = record["evidence"]["fieldOwnership"]
        ownership.update(status="passed", observed=list(ownership["expected"]))
        for item in record["negativeCases"]:
            item.update(status="passed", observedClass=item["expectedClass"])
        record["preservation"]["currentCluster"].update(status="passed", detail="synthetic unchanged fixture")
        record["preservation"]["existingData"].update(status="passed", detail="synthetic unchanged fixture")
        first, rerun, resume = record["runs"]
        first.update(status="passed", completedSteps=list(contract.STEPS))
        rerun.update(status="passed", completedSteps=list(contract.STEPS), verifiedOnly=True)
        resume.update(
            status="passed", completedSteps=list(contract.STEPS),
            interruptedStep="application", resumedAtStep="application",
            retriedSteps=["application", "acceptance"],
        )
        return record, profile

    def validate_complete(self, record, profile):
        record_path, profile_path, _ = self.files(record, profile)
        return contract.validate(record_path, require_complete=True, profile_path=profile_path)

    def test_checked_record_is_explicitly_unrun(self):
        record, _ = contract.validate()
        self.assertFalse(record["metadata"]["liveEvidence"])
        self.assertTrue(all(run["status"] == "unrun" for run in record["runs"]))
        with self.assertRaises((contract.AcceptanceError, contract.profile_contract.ContractError)):
            contract.validate(require_complete=True)

    def test_synthetic_complete_contract(self):
        self.validate_complete(*self.approved())

    def test_unrun_record_cannot_claim_live_result(self):
        value = copy.deepcopy(self.pending)
        value["evidence"]["readiness"][0].update(status="passed", detail="not observed")
        record_path, profile_path, _ = self.files(value)
        with self.assertRaisesRegex(contract.AcceptanceError, "unrun-claims-result"):
            contract.validate(record_path, profile_path=profile_path)

    def test_missing_and_wrong_secret_negatives_are_required(self):
        record, profile = self.approved()
        record["negativeCases"] = [item for item in record["negativeCases"] if item["id"] != "missing-secret"]
        with self.assertRaisesRegex(contract.AcceptanceError, "schema:negativeCases|negative-case-set"):
            self.validate_complete(record, profile)
        record, profile = self.approved()
        wrong = next(item for item in record["negativeCases"] if item["id"] == "wrong-secret")
        wrong["observedClass"] = "accepted"
        with self.assertRaisesRegex(contract.AcceptanceError, "negative-result"):
            self.validate_complete(record, profile)

    def test_wrong_architecture_fails_complete_record(self):
        record, profile = self.approved()
        record["evidence"]["images"][0]["actualArchitecture"] = "linux/arm64"
        with self.assertRaisesRegex(contract.AcceptanceError, "image-observed"):
            self.validate_complete(record, profile)

    def test_unready_dependency_fails_complete_record(self):
        record, profile = self.approved()
        dependency = next(item for item in record["evidence"]["readiness"] if item["id"] == "dependencies-ready")
        dependency.update(status="failed", detail="dependency remained unready")
        with self.assertRaisesRegex(contract.AcceptanceError, "readiness-incomplete"):
            self.validate_complete(record, profile)

    def test_healthy_root_with_empty_child_fails_complete_record(self):
        record, profile = self.approved()
        record["evidence"]["resources"]["observed"] = []
        with self.assertRaisesRegex(contract.AcceptanceError, "resources-observed"):
            self.validate_complete(record, profile)

    def test_pvc_is_a_required_observed_resource(self):
        self.assertIn(contract.EXPECTED_PVC, self.pending["evidence"]["resources"]["expected"])
        record, profile = self.approved()
        record["evidence"]["resources"]["expected"].remove(contract.EXPECTED_PVC)
        record["evidence"]["resources"]["observed"].remove(contract.EXPECTED_PVC)
        with self.assertRaisesRegex(contract.AcceptanceError, "pvc-resource-required"):
            self.validate_complete(record, profile)

    def test_progressive_delivery_requires_every_live_result(self):
        for check_id in contract.PROGRESSIVE_DELIVERY:
            record, profile = self.approved()
            check = next(item for item in record["evidence"]["progressiveDelivery"] if item["id"] == check_id)
            check.update(status="failed", detail=f"synthetic {check_id} failure")
            with self.subTest(check_id=check_id), self.assertRaisesRegex(
                    contract.AcceptanceError, "progressive-delivery-incomplete"):
                self.validate_complete(record, profile)

    def test_database_authorization_missing_false_and_inconsistent_reject(self):
        record, profile = self.approved()
        record["evidence"]["databaseAuthorization"].pop()
        with self.assertRaisesRegex(contract.AcceptanceError, "schema:|database-authorization-set"):
            self.validate_complete(record, profile)
        record, profile = self.approved()
        runtime_ddl = next(item for item in record["evidence"]["databaseAuthorization"]
                           if item["id"] == "runtime-ddl-owned-schema")
        runtime_ddl.update(status="failed", observed="allow", detail="synthetic privilege escape")
        with self.assertRaisesRegex(contract.AcceptanceError, "database-authorization-incomplete"):
            self.validate_complete(record, profile)
        record, profile = self.approved()
        runtime_ddl = next(item for item in record["evidence"]["databaseAuthorization"]
                           if item["id"] == "runtime-ddl-owned-schema")
        runtime_ddl["expected"] = "allow"
        with self.assertRaisesRegex(contract.AcceptanceError, "database-authorization-contract"):
            self.validate_complete(record, profile)

    def test_temporary_binding_and_process_cleanup_are_mandatory(self):
        record, profile = self.approved()
        record["evidence"]["temporaryBindings"]["observedRemoved"].pop()
        with self.assertRaisesRegex(contract.AcceptanceError, "temporary-binding-result"):
            self.validate_complete(record, profile)
        for check_id in contract.PROCESS_CLEANUP:
            record, profile = self.approved()
            check = next(item for item in record["evidence"]["processCleanup"] if item["id"] == check_id)
            check.update(status="failed", detail=f"synthetic {check_id} remained")
            with self.subTest(check_id=check_id), self.assertRaisesRegex(
                    contract.AcceptanceError, "process-cleanup-incomplete"):
                self.validate_complete(record, profile)

    def test_fixed_revisions_and_profile_digest_are_required(self):
        value = copy.deepcopy(self.pending)
        value["profile"]["repositories"]["backend-app"] = "0" * 40
        record_path, profile_path, _ = self.files(value)
        with self.assertRaisesRegex(contract.AcceptanceError, "repository-revisions"):
            contract.validate(record_path, profile_path=profile_path)
        record, profile = self.approved()
        record_path, profile_path, value = self.files(record, profile)
        value["profile"]["sha256"] = "0" * 64
        record_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(contract.AcceptanceError, "profile-digest"):
            contract.validate(record_path, profile_path=profile_path)

    def test_rerun_and_resume_are_bounded(self):
        record, profile = self.approved()
        record["runs"][1]["verifiedOnly"] = False
        with self.assertRaisesRegex(contract.AcceptanceError, "rerun-not-idempotent"):
            self.validate_complete(record, profile)
        record, profile = self.approved()
        record["runs"][2]["retriedSteps"] = ["migration", "application", "acceptance"]
        with self.assertRaisesRegex(contract.AcceptanceError, "resume-retried-steps"):
            self.validate_complete(record, profile)
        record, profile = self.approved()
        record["runs"][2]["migrationJobRecreated"] = True
        with self.assertRaisesRegex(contract.AcceptanceError, "migration-job-recreated"):
            self.validate_complete(record, profile)

    def test_current_cluster_and_secret_material_are_rejected(self):
        record, profile = self.approved()
        profile["spec"]["target"].update(clusterName=contract.CURRENT_CLUSTER, context=contract.CURRENT_CLUSTER)
        record["target"].update(clusterName=contract.CURRENT_CLUSTER, context=contract.CURRENT_CLUSTER)
        with self.assertRaises((contract.AcceptanceError, contract.profile_contract.ContractError)):
            self.validate_complete(record, profile)
        value = copy.deepcopy(self.pending)
        value["preservation"]["password"] = "synthetic"
        record_path, profile_path, _ = self.files(value)
        with self.assertRaises(contract.AcceptanceError):
            contract.validate(record_path, profile_path=profile_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
