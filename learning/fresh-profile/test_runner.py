#!/usr/bin/env python3
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("fresh_runner", HERE / "runner.py")
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
PLATFORM = "1" * 40
APPLICATION = "2" * 40


class FakeExecutor:
    def __init__(self, fail_verify=None, fail_run=None):
        self.verify_calls = []
        self.run_calls = []
        self.fail_verify = fail_verify
        self.fail_run = fail_run

    def verify(self, step, checkpoint):
        self.verify_calls.append(step)
        if step == self.fail_verify:
            raise RuntimeError("synthetic verify failure")
        return {"verified": True}

    def run(self, step):
        self.run_calls.append(step)
        if step == self.fail_run:
            raise RuntimeError("synthetic run failure")
        return {"succeeded": True, "resourceIdentities": [f"fixture/{step}"]}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(tempfile.mkdtemp(prefix="runner-test-", dir=HERE))
        self.addCleanup(shutil.rmtree, self.temporary, True)

    def fixture(self):
        profile = {
            "apiVersion": "platform.lab/v1alpha1", "kind": "FreshProfileLock",
            "metadata": {"name": "account-lightweight-fresh"},
            "spec": {
                "scope": {"cutoverIncluded": False},
                "target": {"clusterName": "learning-fresh-plat217-20261025",
                           "context": "kind-learning-fresh-plat217-20261025",
                           "isolation": "separate-named-sandbox"},
                "repositories": {
                    "platform-gitops": {"commit": PLATFORM},
                    "application-manifest": {"commit": APPLICATION},
                },
                "decisions": {gate: {"status": "approved", "choice": "fixture"}
                              for gate in ("G1", "G2", "G3")},
                "imageSupply": {"decision": "retained-oci-archive", "immutable": True},
            },
        }
        profile_path = self.temporary / "profile.lock.yaml"
        profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
        profile_digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        materialization = {
            "schemaVersion": "platform.lab/materialization/v1alpha1",
            "platformGitops": PLATFORM,
            "applicationManifest": APPLICATION,
            "liveApproved": False,
            "files": {"profile.lock.yaml": profile_digest},
            "sourceFiles": {"platform/root/children/applications.yaml": "4" * 64,
                            "application/release-definition/Chart.yaml": "5" * 64},
        }
        materialization_path = self.temporary / "materialization.json"
        materialization_path.write_text(json.dumps(materialization, sort_keys=True), encoding="utf-8")
        materialization_digest = hashlib.sha256(materialization_path.read_bytes()).hexdigest()
        approval = {
            "apiVersion": "platform.lab/v1alpha1", "kind": "FreshExecutionApproval",
            "metadata": {"name": "account-lightweight-fresh", "approvedBy": "fixture-reviewer",
                         "approvedAt": "2026-10-25T11:00:00Z", "expiresAt": "2026-10-25T13:00:00Z"},
            "spec": {
                "approved": True,
                "target": {"clusterName": "learning-fresh-plat217-20261025",
                           "context": "kind-learning-fresh-plat217-20261025",
                           "profileName": "account-lightweight-fresh"},
                "repositories": {"platformGitops": PLATFORM, "applicationManifest": APPLICATION},
                "hashes": {"profileSha256": profile_digest,
                           "materializationSha256": materialization_digest},
                "scope": copy.deepcopy(runner.SCOPE),
                "gates": {gate: {"approved": True, "approvalId": f"fixture-{gate}"}
                          for gate in runner.GATES},
                "allowedSteps": list(runner.STEPS),
            },
        }
        approval_path = self.temporary / "approval.json"
        approval_path.write_text(json.dumps(approval), encoding="utf-8")
        return approval, approval_path, materialization_path, profile_path, self.temporary / "journal.json"

    def rewrite_approval(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_repository_dag_is_the_runner_step_machine(self):
        dag = runner.validate_dag()
        self.assertEqual([item["id"] for item in dag["spec"]["steps"]], list(runner.STEPS))

    def test_dry_run_is_read_only_and_needs_no_live_inputs(self):
        executor = FakeExecutor()
        missing = self.temporary / "does-not-exist"
        plan = runner.execute("dry-run", missing, missing, missing, self.temporary / "journal.json", executor)
        self.assertEqual(plan["steps"], list(runner.STEPS))
        self.assertEqual(executor.run_calls, [])
        self.assertEqual(executor.verify_calls, [])
        self.assertFalse((self.temporary / "journal.json").exists())

    def test_denied_expired_or_mismatched_gate_has_zero_executor_calls(self):
        for mutation, reason in (
            (lambda value: value["spec"].update(approved=False), "approval-not-granted"),
            (lambda value: value["metadata"].update(expiresAt="2026-10-25T11:30:00Z"), "approval-time-window"),
            (lambda value: value["spec"]["repositories"].update(applicationManifest="3" * 40),
             "materialization-repositories"),
            (lambda value: value["spec"].update(scope={**runner.SCOPE, "cutover": True}), "approval-scope"),
        ):
            with self.subTest(reason=reason):
                approval, approval_path, materialization_path, profile_path, journal_path = self.fixture()
                mutation(approval)
                self.rewrite_approval(approval_path, approval)
                executor = FakeExecutor()
                with self.assertRaisesRegex(runner.RunnerRejected, reason):
                    runner.execute("first", approval_path, materialization_path, profile_path,
                                   journal_path, executor, now=NOW)
                self.assertEqual(executor.run_calls, [])
                self.assertEqual(executor.verify_calls, [])
                self.assertFalse(journal_path.exists())

    def test_pending_materialized_input_has_zero_executor_calls(self):
        approval, approval_path, materialization_path, profile_path, journal_path = self.fixture()
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        profile["spec"]["runtimeImage"] = "PENDING_PLAT211_VERIFIED_IMAGE"
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        profile_digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
        materialization["files"]["profile.lock.yaml"] = profile_digest
        materialization_path.write_text(json.dumps(materialization, sort_keys=True), encoding="utf-8")
        approval["spec"]["hashes"].update(
            profileSha256=profile_digest,
            materializationSha256=hashlib.sha256(materialization_path.read_bytes()).hexdigest())
        self.rewrite_approval(approval_path, approval)
        executor = FakeExecutor()
        with self.assertRaisesRegex(runner.RunnerRejected, "profile-pending-input"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, now=NOW)
        self.assertEqual(executor.run_calls, [])
        self.assertEqual(executor.verify_calls, [])

    def test_first_then_rerun_only_verifies(self):
        _, approval_path, materialization_path, profile_path, journal_path = self.fixture()
        first = FakeExecutor()
        journal = runner.execute("first", approval_path, materialization_path, profile_path,
                                 journal_path, first, now=NOW)
        self.assertEqual(first.run_calls, list(runner.STEPS))
        self.assertEqual(first.verify_calls, [])
        self.assertTrue(all(item["status"] == "succeeded" for item in journal["steps"].values()))
        rerun = FakeExecutor()
        runner.execute("rerun", approval_path, materialization_path, profile_path,
                       journal_path, rerun, now=NOW)
        self.assertEqual(rerun.verify_calls, list(runner.STEPS))
        self.assertEqual(rerun.run_calls, [])

    def test_resume_retries_only_incomplete_and_preserves_succeeded_jobs(self):
        _, approval_path, materialization_path, profile_path, journal_path = self.fixture()
        runner.execute("first", approval_path, materialization_path, profile_path,
                       journal_path, FakeExecutor(), now=NOW)
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["steps"]["migration"].update(status="failed", errorClass="synthetic")
        for step in ("application", "acceptance"):
            journal["steps"][step].update(status="pending", evidence=None, errorClass=None, attempts=0)
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        resumed = FakeExecutor()
        runner.execute("resume", approval_path, materialization_path, profile_path,
                       journal_path, resumed, now=NOW)
        self.assertIn("role-bootstrap", resumed.verify_calls)
        self.assertNotIn("role-bootstrap", resumed.run_calls)
        self.assertEqual(resumed.run_calls, ["migration", "application", "acceptance"])

    def test_completed_job_verification_failure_never_recreates_job(self):
        _, approval_path, materialization_path, profile_path, journal_path = self.fixture()
        runner.execute("first", approval_path, materialization_path, profile_path,
                       journal_path, FakeExecutor(), now=NOW)
        executor = FakeExecutor(fail_verify="role-bootstrap")
        with self.assertRaisesRegex(runner.RunnerRejected, "completed-job-verification:role-bootstrap"):
            runner.execute("resume", approval_path, materialization_path, profile_path,
                           journal_path, executor, now=NOW)
        self.assertEqual(executor.run_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

