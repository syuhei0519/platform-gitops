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
        record = copy.deepcopy(self.pending)
        record["metadata"].update(recordedAt="2026-10-25T21:00:00Z", liveEvidence=True)
        record["target"].update(
            clusterName="kind-platform-fresh-plat217",
            context="kind-platform-fresh-plat217",
            clusterUID="fixture-cluster-uid",
        )
        for image in record["evidence"]["images"]:
            image.update(
                status="passed", retrievedDigest=image["expectedDigest"],
                actualArchitecture=image["expectedArchitecture"],
                sourceType="retained-oci-archive",
            )
        resources = record["evidence"]["resources"]
        resources.update(status="passed", observed=list(resources["expected"]))
        revisions = record["evidence"]["revisions"]
        revisions.update(status="passed", actual=copy.deepcopy(revisions["desired"]))
        for item in record["evidence"]["readiness"] + record["evidence"]["resyncRestart"]:
            item.update(status="passed", detail="synthetic unit fixture")
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
