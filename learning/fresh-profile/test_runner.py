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
    plan_sha256 = None

    def __init__(self, fail_verify=None, fail_run=None, cluster_uid=None,
                 reconcile_states=None):
        self.identity_calls = []
        self.verify_calls = []
        self.reconcile_calls = []
        self.run_calls = []
        self.fail_verify = fail_verify
        self.fail_run = fail_run
        self.cluster_uid = cluster_uid
        self.reconcile_states = reconcile_states or {}

    @staticmethod
    def digest(step):
        return hashlib.sha256(("resource:" + step).encode()).hexdigest()

    def identity(self, target):
        self.identity_calls.append(target["context"])
        return {"context": target["context"], "clusterUID": self.cluster_uid}

    def verify(self, step, checkpoint):
        self.verify_calls.append(step)
        if step == self.fail_verify:
            raise RuntimeError("synthetic verify failure")
        return {"verified": True, "resourceDigest": checkpoint["resourceDigest"]}

    def reconcile(self, step, checkpoint):
        self.reconcile_calls.append(step)
        result = self.reconcile_states.get(step, "incomplete")
        if result == "succeeded":
            evidence = {"succeeded": True, "resourceIdentities": [f"fixture/{step}"]}
            if step == "cluster":
                evidence["clusterUID"] = self.cluster_uid or "fixture-cluster-uid"
                self.cluster_uid = evidence["clusterUID"]
            return {"state": "succeeded", "resourceDigest": self.digest(step),
                    "evidence": evidence}
        return {"state": "incomplete"}

    def run(self, step):
        self.run_calls.append(step)
        if step == self.fail_run:
            raise RuntimeError("synthetic run failure")
        evidence = {"succeeded": True, "resourceIdentities": [f"fixture/{step}"],
                    "resourceDigest": self.digest(step)}
        if step == "cluster":
            self.cluster_uid = "fixture-cluster-uid"
            evidence["clusterUID"] = self.cluster_uid
        return evidence


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(tempfile.mkdtemp(prefix="runner-test-", dir=HERE))
        self.addCleanup(shutil.rmtree, self.temporary, True)
        self.fixture_index = 0

    def fixture(self):
        self.fixture_index += 1
        fixture_name = f"fixture-{self.fixture_index}"
        profile = yaml.safe_load((HERE / "profile.lock.yaml").read_text(encoding="utf-8"))
        spec = profile["spec"]
        spec["target"].update(clusterName="learning-fresh-plat217-20261025",
                              context="kind-learning-fresh-plat217-20261025")
        spec["repositories"]["platform-gitops"]["commit"] = PLATFORM
        spec["repositories"]["application-manifest"]["commit"] = APPLICATION
        spec["freshRuntimeRequirement"]["admissionSecretKeyContract"] = \
            "username-password-operation-key-allowlist-aligned"
        for gate in ("G1", "G2", "G3"):
            spec["decisions"][gate] = {"status": "approved", "choice": "retained-oci-archive"}
        spec["imageSupply"].update(decision="retained-oci-archive", immutable=True,
                                   retentionUntil="2026-11-24", restoreOwner="espoi")
        next(item for item in spec["images"] if item["name"] == "account-runtime")["ref"] = (
            "ghcr.io/syuhei0519/backend-app@sha256:" + "b" * 64)
        for index, item in enumerate(spec["inputs"]["secrets"]):
            digest_value = f"{index + 1:064x}"
            item.update(owner="espoi", expiry="2026-11-24",
                        sourceRef="sealed-input:sha256:" + digest_value)
        limits = spec["budget"]["approvalLimits"]
        limits.update(elapsedMaxMinutes=60, hostAvailableMemoryMinGiB=8,
                      hostFreeDiskMinGiB=400, dockerAdditionalDiskMaxGiB=10,
                      workloadCPURequestsMaxMilli=2475, workloadMemoryRequestsMaxMiB=4608,
                      imageBytesMaxGiB=10, podPendingMaxSeconds=120)
        spec["budget"]["estimates"].update(
            retainedArchiveBytes="approved:15378944",
            controlPlaneOverhead="approved:2000m-cpu-1634Mi-memory")

        bundle = self.temporary / fixture_name / "sealed"
        bundle.parent.mkdir()
        bundle.mkdir()
        profile_path = bundle / "profile.lock.yaml"
        profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
        profile_digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()

        supply = yaml.safe_load((HERE / "image-supply.lock.yaml").read_text(encoding="utf-8"))
        supply["spec"]["decision"] = {
            "status": "approved", "selectedMode": "retained-oci-archive",
            "restoreOwner": "espoi", "retentionUntil": "2026-11-24",
            "restoreProcedureRef": "runbook:restore-fresh-image-supply",
        }
        profile_images = {item["name"]: item["ref"] for item in spec["images"]}
        for item in supply["spec"]["images"]:
            expected = profile_images[item["name"]].rsplit("@", 1)[1]
            item["source"] = {
                "kind": "retained-oci-archive", "published": False, "immutable": True,
                "freshEligible": True, "location": f"archive-ref:fresh/{item['name']}.oci.tar",
                "archiveSha256": "sha256:" + "a" * 64,
                "manifest": {"kind": "single-platform", "manifestDigest": expected,
                             "os": "linux", "architecture": "amd64"},
            }
            item["retrieval"] = {"status": "succeeded", "emptyNodeCache": True,
                                 "observedDigest": expected,
                                 "observedArchitecture": "linux/amd64"}
            item["checks"] = {
                name: {"status": "passed", "evidenceRef": f"evidence:{item['name']}:{name}"}
                for name in ("digest", "architecture", "entrypoint", "user", "args",
                             "migration", "smoke", "scan", "sbom")
            }
        supply_path = bundle / "image-supply.lock.yaml"
        supply_path.write_text(yaml.safe_dump(supply, sort_keys=False), encoding="utf-8")

        for relative in runner.REQUIRED_SEALED_FILES - {"profile.lock.yaml", "image-supply.lock.yaml"}:
            path = bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == "bootstrap-dag.yaml":
                shutil.copyfile(HERE / "bootstrap-dag.yaml", path)
            else:
                path.write_text("{}\n", encoding="utf-8")

        platform_source = self.temporary / fixture_name / "platform-source"
        application_source = self.temporary / fixture_name / "application-source"
        source_files = {}
        source_roots = {"platform-gitops": platform_source,
                        "application-manifest": application_source}
        source_revisions = {"platform-gitops": PLATFORM,
                            "application-manifest": APPLICATION}
        for repository, paths in runner.REQUIRED_SOURCE_PATHS.items():
            for relative in paths:
                source_blob = source_roots[repository] / relative
                source_blob.parent.mkdir(parents=True, exist_ok=True)
                source_blob.write_text(f"{repository}:{relative}\n", encoding="utf-8")
                source_files[f"{repository}@{source_revisions[repository]}:{relative}"] = (
                    hashlib.sha256(source_blob.read_bytes()).hexdigest())
        files = {relative: hashlib.sha256((bundle / relative).read_bytes()).hexdigest()
                 for relative in runner.REQUIRED_SEALED_FILES}
        materialization = {
            "schemaVersion": "platform.lab/materialization/v1alpha1",
            "platformGitops": PLATFORM,
            "applicationManifest": APPLICATION,
            "liveApproved": False,
            "files": files,
            "sourceFiles": source_files,
        }
        materialization_path = bundle / "materialization.json"
        materialization_path.write_text(json.dumps(materialization, sort_keys=True), encoding="utf-8")
        materialization_digest = hashlib.sha256(materialization_path.read_bytes()).hexdigest()
        adapter_plan_path = bundle / "live-step-plan.json"
        tool_versions = {
            alias: spec["tools"][profile_key]
            for alias, profile_key in runner.TOOL_VERSION_KEYS.items()
        }
        tools = {}
        tool_dir = self.temporary / fixture_name / "tools"
        tool_dir.mkdir()
        for alias, version in tool_versions.items():
            tool_path = tool_dir / (alias + ".fixture")
            tool_path.write_text(f"{alias}:{version}\n", encoding="utf-8")
            tools[alias] = {"path": str(tool_path.resolve()),
                            "sha256": hashlib.sha256(tool_path.read_bytes()).hexdigest(),
                            "version": version}
        kubeconfig = str((self.temporary / fixture_name / "fresh.kubeconfig").resolve())
        target_context = spec["target"]["context"]
        resource_tokens = {
            "Deployment": "deployment", "Job": "job", "PersistentVolumeClaim": "pvc",
            "StatefulSet": "statefulset", "Rollout": "rollout", "Service": "service",
            "DestinationRule": "destinationrule", "VirtualService": "virtualservice",
        }
        def resource_spec(kind, name):
            if kind == "Job":
                return {"template": {"metadata": {"labels": {"step": name}}}}
            if kind == "PersistentVolumeClaim":
                return {"accessModes": ["ReadWriteOnce"], "volumeName": "pvc-account-data"}
            if kind in {"Deployment", "StatefulSet", "Rollout"}:
                return {"replicas": 1, "template": {"metadata": {"labels": {"step": name}}}}
            return {"selector": {"app": name}}
        def command(kind, namespace, name):
            return {"tool": "kubectl", "args": ["--kubeconfig", kubeconfig,
                    "--context", target_context, "get", resource_tokens[kind], name,
                    "--namespace", namespace, "--output", "json"]}
        def step_contract(step):
            if step == "database":
                contracts = [
                    ("v1", "PersistentVolumeClaim", "fresh-account", "account-data",
                     "none", "pvc-bound", {}),
                    ("apps/v1", "StatefulSet", "fresh-account", "account-postgresql",
                     "observed-current", "statefulset-ready", {}),
                ]
            elif step == "application":
                contracts = [
                    ("argoproj.io/v1alpha1", "Rollout", "fresh-account", "account-api",
                     "observed-current", "rollout-ready", {"Available": "True"}),
                    ("v1", "Service", "fresh-account", "account-api", "none", "none", {}),
                    ("networking.istio.io/v1beta1", "DestinationRule", "fresh-account",
                     "account-api", "none", "none", {}),
                    ("networking.istio.io/v1beta1", "VirtualService", "fresh-account",
                     "account-api", "none", "none", {}),
                ]
            elif step in {"role-bootstrap", "migration"}:
                name = "account-db-role-bootstrap" if step == "role-bootstrap" else "account-migration"
                contracts = [("batch/v1", "Job", "fresh-account", name,
                              "none", "job-complete", {"Complete": "True"})]
            else:
                contracts = [("apps/v1", "Deployment", "fresh-control", step,
                              "observed-current", "workload-ready", {"Available": "True"})]
            observations, identities, verify, manifests = [], [], [], []
            for api_version, kind, namespace, name, generation, health, conditions in contracts:
                identity = f"{kind}/{namespace}/{name}"
                desired = {"/spec": resource_spec(kind, name)}
                identities.append(identity)
                verify.append(command(kind, namespace, name))
                observations.append({
                    "apiVersion": api_version, "identity": identity,
                    "desiredPaths": ["/spec"],
                    "desiredSha256": hashlib.sha256(json.dumps(
                        desired, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                    "generationPolicy": generation, "healthPolicy": health,
                    "requiredConditions": conditions,
                })
                manifests.append({"apiVersion": api_version, "kind": kind,
                                  "metadata": {"namespace": namespace, "name": name},
                                  "spec": resource_spec(kind, name)})
            manifest_path = bundle / f"{step}-apply.json"
            manifest_path.write_text(json.dumps({"apiVersion": "v1", "kind": "List",
                                                  "items": manifests}), encoding="utf-8")
            run = {"tool": "kubectl", "args": ["--kubeconfig", kubeconfig,
                   "--context", target_context, "apply", "--filename",
                   str(manifest_path.resolve())]}
            return {"verify": verify, "reconcile": copy.deepcopy(verify), "run": [run],
                    "resourceIdentities": identities, "observations": observations}
        adapter_plan = {
            "schemaVersion": "platform.lab/approved-live-step-plan/v2",
            "target": {"clusterName": spec["target"]["clusterName"],
                       "context": target_context, "kubeconfig": kubeconfig},
            "tools": tools,
            "identity": {"clusterUID": {"tool": "kubectl", "args": [
                "--kubeconfig", kubeconfig, "--context", target_context,
                "get", "namespace", "kube-system", "--output",
                "jsonpath={.metadata.uid}"]}},
            "steps": {step: step_contract(step) for step in runner.STEPS},
        }
        adapter_plan_path.write_text(json.dumps(adapter_plan) + "\n", encoding="utf-8")
        FakeExecutor.plan_sha256 = hashlib.sha256(adapter_plan_path.read_bytes()).hexdigest()
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
                           "materializationSha256": materialization_digest,
                           "sourceSetSha256": runner._mapping_digest(source_files),
                           "liveAdapterPlanSha256": hashlib.sha256(adapter_plan_path.read_bytes()).hexdigest()},
                "scope": copy.deepcopy(runner.SCOPE),
                "gates": {gate: {"approved": True, "approvalId": f"fixture-{gate}"}
                          for gate in runner.GATES},
                "inputDigests": {},
                "allowedSteps": list(runner.STEPS),
            },
        }
        references = {spec["inputs"]["clusterKubeconfigRef"], spec["inputs"]["imageSupplyRef"]}
        references.update(item["sourceRef"] for item in spec["inputs"]["secrets"])
        for reference in references:
            approval["spec"]["inputDigests"][reference] = (
                reference.rsplit(":", 1)[1] if reference.startswith("sealed-input:sha256:") else "9" * 64)
        approval_path = self.temporary / fixture_name / "approval.json"
        approval_path.write_text(json.dumps(approval), encoding="utf-8")
        return (approval, approval_path, materialization_path, profile_path,
                self.temporary / fixture_name / "journal.json", platform_source, application_source)

    def rewrite_approval(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def reseal(self, approval, approval_path, materialization_path, profile_path):
        materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
        bundle = materialization_path.parent
        materialization["files"] = {
            relative: hashlib.sha256((bundle / relative).read_bytes()).hexdigest()
            for relative in materialization["files"]
        }
        materialization_path.write_text(json.dumps(materialization, sort_keys=True), encoding="utf-8")
        approval["spec"]["hashes"].update(
            profileSha256=hashlib.sha256(profile_path.read_bytes()).hexdigest(),
            materializationSha256=hashlib.sha256(materialization_path.read_bytes()).hexdigest(),
            sourceSetSha256=runner._mapping_digest(materialization["sourceFiles"]),
        )
        self.rewrite_approval(approval_path, approval)

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
                (approval, approval_path, materialization_path, profile_path, journal_path,
                 platform_source, application_source) = self.fixture()
                mutation(approval)
                self.rewrite_approval(approval_path, approval)
                executor = FakeExecutor()
                with self.assertRaisesRegex(runner.RunnerRejected, reason):
                    runner.execute("first", approval_path, materialization_path, profile_path,
                                   journal_path, executor, platform_source, application_source,
                                   now=NOW)
                self.assertEqual(executor.run_calls, [])
                self.assertEqual(executor.verify_calls, [])
                self.assertFalse(journal_path.exists())

    def test_executor_must_be_bound_to_the_approved_live_plan(self):
        (_, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        executor = FakeExecutor()
        executor.plan_sha256 = "0" * 64
        with self.assertRaisesRegex(runner.RunnerRejected, "executor-plan-binding"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source, now=NOW)
        self.assertEqual(executor.identity_calls, [])
        self.assertEqual(executor.run_calls, [])

    def test_pending_materialized_input_has_zero_executor_calls(self):
        (approval, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        profile["spec"]["runtimeImage"] = "PENDING_PLAT211_VERIFIED_IMAGE"
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        self.reseal(approval, approval_path, materialization_path, profile_path)
        executor = FakeExecutor()
        with self.assertRaisesRegex(runner.RunnerRejected, "profile-pending-input"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source, now=NOW)
        self.assertEqual(executor.run_calls, [])
        self.assertEqual(executor.verify_calls, [])

    def test_first_then_rerun_only_verifies(self):
        (_, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        first = FakeExecutor()
        journal = runner.execute("first", approval_path, materialization_path, profile_path,
                                 journal_path, first, platform_source, application_source, now=NOW)
        self.assertEqual(first.run_calls, list(runner.STEPS))
        self.assertEqual(first.verify_calls, [])
        self.assertTrue(all(item["status"] == "succeeded" for item in journal["steps"].values()))
        rerun = FakeExecutor(cluster_uid="fixture-cluster-uid")
        runner.execute("rerun", approval_path, materialization_path, profile_path,
                       journal_path, rerun, platform_source, application_source, now=NOW)
        self.assertEqual(rerun.verify_calls, list(runner.STEPS))
        self.assertEqual(rerun.run_calls, [])

    def test_resume_retries_only_incomplete_and_preserves_succeeded_jobs(self):
        (_, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        runner.execute("first", approval_path, materialization_path, profile_path,
                       journal_path, FakeExecutor(), platform_source, application_source, now=NOW)
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["steps"]["migration"].update(status="failed", errorClass="synthetic")
        for step in ("application", "acceptance"):
            journal["steps"][step].update(status="pending", evidence=None, errorClass=None, attempts=0)
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        resumed = FakeExecutor(cluster_uid="fixture-cluster-uid",
                               reconcile_states={"migration": "succeeded"})
        runner.execute("resume", approval_path, materialization_path, profile_path,
                       journal_path, resumed, platform_source, application_source, now=NOW)
        self.assertIn("role-bootstrap", resumed.verify_calls)
        self.assertNotIn("role-bootstrap", resumed.run_calls)
        self.assertIn("migration", resumed.reconcile_calls)
        self.assertNotIn("migration", resumed.run_calls)
        self.assertEqual(resumed.run_calls, ["application", "acceptance"])

    def test_completed_job_verification_failure_never_recreates_job(self):
        (_, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        runner.execute("first", approval_path, materialization_path, profile_path,
                       journal_path, FakeExecutor(), platform_source, application_source, now=NOW)
        executor = FakeExecutor(fail_verify="role-bootstrap", cluster_uid="fixture-cluster-uid")
        with self.assertRaisesRegex(runner.RunnerRejected, "completed-job-verification:role-bootstrap"):
            runner.execute("resume", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source, now=NOW)
        self.assertEqual(executor.run_calls, [])

    def test_job_success_after_crash_is_reconciled_without_recreation(self):
        (_, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        runner.execute("first", approval_path, materialization_path, profile_path,
                       journal_path, FakeExecutor(), platform_source, application_source, now=NOW)
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["steps"]["migration"].update(status="running", evidence=None,
                                                resourceDigest=None,
                                                errorClass="process-ended-before-checkpoint")
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        executor = FakeExecutor(cluster_uid="fixture-cluster-uid",
                                reconcile_states={"migration": "succeeded"})
        result = runner.execute("resume", approval_path, materialization_path, profile_path,
                                journal_path, executor, platform_source, application_source, now=NOW)
        self.assertIn("migration", executor.reconcile_calls)
        self.assertNotIn("migration", executor.run_calls)
        self.assertEqual(result["steps"]["migration"]["status"], "succeeded")
        self.assertEqual(result["steps"]["migration"]["resourceDigest"],
                         FakeExecutor.digest("migration"))

    def test_uncertain_job_without_success_proof_is_never_recreated(self):
        (_, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        runner.execute("first", approval_path, materialization_path, profile_path,
                       journal_path, FakeExecutor(), platform_source, application_source, now=NOW)
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["steps"]["role-bootstrap"].update(status="running", evidence=None,
                                                     resourceDigest=None,
                                                     errorClass="process-ended-before-checkpoint")
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        executor = FakeExecutor(cluster_uid="fixture-cluster-uid")
        with self.assertRaisesRegex(runner.RunnerRejected,
                                    "uncertain-job-outcome:role-bootstrap"):
            runner.execute("resume", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source, now=NOW)
        self.assertNotIn("role-bootstrap", executor.run_calls)

    def test_journal_rejects_same_name_different_cluster_and_changed_inputs(self):
        (_, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        runner.execute("first", approval_path, materialization_path, profile_path,
                       journal_path, FakeExecutor(), platform_source, application_source, now=NOW)
        different = FakeExecutor(cluster_uid="different-cluster-uid")
        with self.assertRaisesRegex(runner.RunnerRejected, "journal-cluster-uid"):
            runner.execute("resume", approval_path, materialization_path, profile_path,
                           journal_path, different, platform_source, application_source, now=NOW)
        self.assertEqual(different.run_calls, [])

        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        key = next(iter(journal["inputDigests"]))
        journal["inputDigests"][key] = "0" * 64
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        same_cluster = FakeExecutor(cluster_uid="fixture-cluster-uid")
        with self.assertRaisesRegex(runner.RunnerRejected, "journal-inputs"):
            runner.execute("resume", approval_path, materialization_path, profile_path,
                           journal_path, same_cluster, platform_source, application_source, now=NOW)
        self.assertEqual(same_cluster.run_calls, [])

        journal["inputDigests"][key] = json.loads(
            approval_path.read_text(encoding="utf-8"))["spec"]["inputDigests"][key]
        journal["liveAdapterPlanSha256"] = "0" * 64
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        plan_changed = FakeExecutor(cluster_uid="fixture-cluster-uid")
        with self.assertRaisesRegex(runner.RunnerRejected, "journal-inputs"):
            runner.execute("resume", approval_path, materialization_path, profile_path,
                           journal_path, plan_changed, platform_source, application_source,
                           now=NOW)
        self.assertEqual(plan_changed.run_calls, [])

    def test_tampered_sealed_security_and_source_blob_fail_before_executor(self):
        for relative, reason in (("security/admission.yaml", "materialization-file-digest"),
                                 ("security/rbac.yaml", "materialization-file-digest")):
            with self.subTest(relative=relative):
                (_, approval_path, materialization_path, profile_path, journal_path,
                 platform_source, application_source) = self.fixture()
                (materialization_path.parent / relative).write_text("tampered\n", encoding="utf-8")
                executor = FakeExecutor()
                with self.assertRaisesRegex(runner.RunnerRejected, reason):
                    runner.execute("first", approval_path, materialization_path, profile_path,
                                   journal_path, executor, platform_source, application_source, now=NOW)
                self.assertEqual(executor.identity_calls, [])
                self.assertEqual(executor.run_calls, [])

        (_, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        (platform_source / "learning/fresh-profile/root/children/applications.yaml").write_text(
            "tampered source\n", encoding="utf-8")
        executor = FakeExecutor()
        with self.assertRaisesRegex(runner.RunnerRejected, "source-file-digest"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source, now=NOW)
        self.assertEqual(executor.identity_calls, [])

        (approval, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
        materialization["sourceFiles"].pop(next(iter(materialization["sourceFiles"])))
        materialization_path.write_text(json.dumps(materialization, sort_keys=True), encoding="utf-8")
        approval["spec"]["hashes"].update(
            materializationSha256=hashlib.sha256(materialization_path.read_bytes()).hexdigest(),
            sourceSetSha256=runner._mapping_digest(materialization["sourceFiles"]),
        )
        self.rewrite_approval(approval_path, approval)
        executor = FakeExecutor()
        with self.assertRaisesRegex(runner.RunnerRejected, "materialization-source-set"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source, now=NOW)
        self.assertEqual(executor.identity_calls, [])

    def test_missing_image_or_budget_fails_full_contract_before_executor(self):
        for mutate in (
            lambda spec: spec["images"].pop(),
            lambda spec: spec["budget"].pop("approvalLimits"),
        ):
            approval, approval_path, materialization_path, profile_path, journal_path, platform_source, application_source = self.fixture()
            profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
            mutate(profile["spec"])
            profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
            self.reseal(approval, approval_path, materialization_path, profile_path)
            executor = FakeExecutor()
            with self.assertRaisesRegex(runner.RunnerRejected, "profile-contract"):
                runner.execute("first", approval_path, materialization_path, profile_path,
                               journal_path, executor, platform_source, application_source, now=NOW)
            self.assertEqual(executor.identity_calls, [])
            self.assertEqual(executor.run_calls, [])

    def test_unneeded_repository_credential_is_rejected(self):
        (approval, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        profile["spec"]["inputs"]["secrets"].append({
            "name": "repository-read", "purpose": "unneeded public Git credential",
            "destination": "fresh-control/repository-read", "owner": "espoi",
            "expiry": "2026-11-24", "sourceRef": "sealed-input:sha256:" + "f" * 64,
        })
        profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
        self.reseal(approval, approval_path, materialization_path, profile_path)
        executor = FakeExecutor()
        with self.assertRaisesRegex(runner.RunnerRejected, "g3-secret-set"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source, now=NOW)
        self.assertEqual(executor.identity_calls, [])
        self.assertEqual(executor.run_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

