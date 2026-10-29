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
from unittest import mock

import yaml
from jsonschema import Draft7Validator


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
    supports_credential_runtime_proof = True

    def __init__(self, fail_verify=None, fail_run=None, cluster_uid=None,
                 reconcile_states=None, credential_proof=True):
        self.identity_calls = []
        self.verify_calls = []
        self.reconcile_calls = []
        self.run_calls = []
        self.fail_verify = fail_verify
        self.fail_run = fail_run
        self.cluster_uid = cluster_uid
        self.reconcile_states = reconcile_states or {}
        self.include_credential_proof = credential_proof

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
        result = {"verified": True, "resourceDigest": checkpoint["resourceDigest"]}
        if step == "credential-bootstrap":
            result["credentialRuntimeProof"] = checkpoint["evidence"][
                "credentialRuntimeProof"]
        return result

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
        if step == "credential-bootstrap" and self.include_credential_proof:
            evidence["credentialRuntimeProof"] = self.credential_proof()
        return evidence

    def credential_proof(self):
        common = {"owner": "fixture-owner", "expiry": "2026-11-24",
                  "issuerRef": "issuer:fixture", "cleanupOwner": "fixture-cleanup"}
        return {
            "schemaVersion": "platform.lab/g3-runtime-proof/v1",
            "observedAt": "2026-10-25T12:00:00Z",
            "clusterUID": self.cluster_uid or "fixture-cluster-uid",
            "secrets": [
                {"name": "account-db-bootstrap",
                 "destination": "fresh-account/account-db-bootstrap",
                 "uid": "uid-bootstrap", "secretType": "Opaque",
                 "keyNames": ["admin-password"], **common,
                 "issuanceReceiptSha256": "4" * 64},
                {"name": "account-db-runtime",
                 "destination": "fresh-account/account-db-runtime",
                 "uid": "uid-runtime", "secretType": "Opaque",
                 "keyNames": ["username", "password"], **common,
                 "issuanceReceiptSha256": "5" * 64},
                {"name": "account-db-migration",
                 "destination": "fresh-account/account-db-migration",
                 "uid": "uid-migration", "secretType": "Opaque",
                 "keyNames": ["username", "password"], **common,
                 "issuanceReceiptSha256": "6" * 64},
            ],
            "ca": {"name": "fixture-fresh-ca", "scope": "fresh-sandbox-only",
                   "uid": "uid-ca", **common, "issuanceReceiptSha256": "7" * 64},
        }


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(tempfile.mkdtemp(prefix="runner-test-", dir=HERE))
        self.addCleanup(shutil.rmtree, self.temporary, True)
        self.fixture_index = 0
        original_loader = runner._load_contract_module

        def load_contract(name):
            module = original_loader(name)
            if name == "approved_live_adapter":
                original_content_sha256 = module._content_sha256
                original_sha256 = module._sha256

                def fixture_content_sha256(path):
                    if Path(path).name == "argo-rollouts-2.43.7.fixture.tgz":
                        return module.ROLLOUTS_CHART_SHA256
                    return original_content_sha256(path)

                def fixture_sha256(path):
                    if Path(path).name == "crds-apply.json":
                        return module.ROLLOUTS_CRD_LIST_SHA256
                    return original_sha256(path)

                module._content_sha256 = fixture_content_sha256
                module._sha256 = fixture_sha256
            return module

        patcher = mock.patch.object(runner, "_load_contract_module", side_effect=load_contract)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fixture(self, plugin=False):
        self.fixture_index += 1
        fixture_name = f"fixture-{self.fixture_index}"
        profile = yaml.safe_load((HERE / "profile.template.yaml").read_text(encoding="utf-8"))
        spec = profile["spec"]
        profile["metadata"]["purpose"] = "runtime-evidence"
        spec["scope"]["currentClusterExcluded"] = "kind-protected-existing"
        spec["target"].update(clusterName="learning-fresh-fixture-sandbox",
                              context="kind-learning-fresh-fixture-sandbox")
        spec["repositories"]["platform-gitops"]["commit"] = PLATFORM
        spec["repositories"]["application-manifest"]["commit"] = APPLICATION
        spec["freshRuntimeRequirement"]["admissionSecretKeyContract"] = \
            "username-password-operation-key-allowlist-aligned"
        for gate in ("G1", "G2", "G3Precondition"):
            spec["decisions"][gate] = {"status": "approved", "choice": "retained-oci-archive"}
        spec["imageSupply"].update(decision="retained-oci-archive", immutable=True,
                                   retentionUntil="2026-11-24", restoreOwner="fixture-owner")
        next(item for item in spec["images"] if item["name"] == "account-runtime")["ref"] = (
            "ghcr.io/syuhei0519/backend-app@sha256:" + "b" * 64)
        plan = spec["inputs"]["credentialIssuancePlan"]
        plan.update(planId="fixture-g3-plan", procedureRef="runbook:fixture-g3-issuance",
                    procedureSha256="3" * 64)
        for item in [*plan["secrets"], plan["ca"]]:
            item.update(owner="fixture-owner", expiry="2026-11-24",
                        issuerRef="issuer:fixture", cleanupOwner="fixture-cleanup")
        plan["ca"]["name"] = "fixture-fresh-ca"
        spec["budget"]["hostObservation"] = {
            "capturedAt": "2026-10-25T11:59:00Z", "logicalCpu": 8,
            "installedMemoryGiB": 16, "availableMemoryGiB": 9,
            "freeDiskGiB": 401, "dockerCpuLimit": 8,
            "dockerMemoryLimitGiB": 12, "dockerImagesGiB": 1,
            "dockerVolumesGiB": 1, "dockerBuildCacheGiB": 1,
            "existingRunningContainers": 0,
        }
        limits = spec["budget"]["approvalLimits"]
        limits.update(elapsedMaxMinutes=60, hostAvailableMemoryMinGiB=8,
                      hostFreeDiskMinGiB=400, dockerAdditionalDiskMaxGiB=10,
                      workloadCPURequestsMaxMilli=2475, workloadMemoryRequestsMaxMiB=4608,
                      imageBytesMaxGiB=10, podPendingMaxSeconds=120)
        spec["budget"]["estimates"].update(
            retainedArchiveBytes="approved:15378944",
            controlPlaneOverhead="approved:2000m-cpu-1634Mi-memory")
        spec["contracts"]["imageSupply"] = "image-supply.lock.yaml"

        bundle = self.temporary / fixture_name / "sealed"
        bundle.parent.mkdir()
        bundle.mkdir()
        plugin_contract = None
        if plugin:
            from plugin_overlay import overlay
            controller_image = "local/account-rollouts-fresh@sha256:" + "a" * 64
            spec["images"].append({"name":"argo-rollouts-plugin", "ref":controller_image, "architecture":"linux/amd64"})
            upstream = runner._load_contract_module("approved_live_adapter").ROLLOUTS_CONTROLLER_IMAGE
            deployment={"apiVersion":"apps/v1", "kind":"Deployment", "metadata":{"name":"argo-rollouts","namespace":"fresh-control"}, "spec":{"template":{"spec":{"serviceAccountName":"argo-rollouts", "containers":[{"name":"controller","image":upstream}]}}}}
            objects=overlay(deployment, controller_image, "b"*64)
            plugin_path=bundle/"plugin-bundle.json"
            plugin_path.write_text(json.dumps({"apiVersion":"v1","kind":"List","items":objects}), encoding="utf-8")
            plugin_contract={"profile":"fresh-account","sourceRevision":APPLICATION,"controllerImage":controller_image,"binarySha256":"b"*64,"bundleSha256":hashlib.sha256(plugin_path.read_bytes()).hexdigest()}
        profile_path = bundle / "profile.lock.yaml"
        profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
        profile_digest = hashlib.sha256(profile_path.read_bytes()).hexdigest()

        supply = yaml.safe_load((HERE / "image-supply.template.yaml").read_text(encoding="utf-8"))
        supply["spec"]["decision"] = {
            "status": "approved", "selectedMode": "retained-oci-archive",
            "restoreOwner": "fixture-owner", "retentionUntil": "2026-11-24",
            "restoreProcedureRef": "runbook:restore-fresh-image-supply",
        }
        if plugin:
            cloned = copy.deepcopy(next(x for x in supply["spec"]["images"] if x["name"] == "argo-rollouts"))
            cloned["name"] = "argo-rollouts-plugin"
            supply["spec"]["images"].append(cloned)
            supply["spec"]["rolloutsPlugin"] = plugin_contract
        profile_images = {item["name"]: item["ref"] for item in spec["images"]}
        archive_paths = {}
        archive_dir = bundle / "approved-sources"
        archive_dir.mkdir()
        for item in supply["spec"]["images"]:
            expected = profile_images[item["name"]].rsplit("@", 1)[1]
            archive_path = archive_dir / (item["name"] + ".oci.tar")
            archive_path.write_bytes(("fixture-archive:" + item["name"]).encode())
            archive_paths[item["name"]] = archive_path
            archive_sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            item["source"] = {
                "kind": "retained-oci-archive", "published": False, "immutable": True,
                "freshEligible": True, "location": f"archive-ref:fresh/{item['name']}.oci.tar",
                "archiveSha256": "sha256:" + archive_sha,
                "manifest": {"kind": "single-platform", "manifestDigest": expected,
                             "os": "linux", "architecture": "amd64"},
            }
            item["retrieval"] = {"status": "succeeded", "emptyNodeCache": True,
                                 "observedDigest": expected,
                                 "observedArchitecture": "linux/amd64"}
            item["checks"] = {
                name: {"status": "passed", "evidenceRef": f"evidence:{item['name']}:{name}"}
                for name in ("digest", "architecture", "entrypoint", "user", "args",
                             "scan", "sbom")
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
                 "reason": "account-runtime-owns-account-schema-migration"}
                if item["name"] == "account-runtime" else {
                    "applicability": "not-applicable", "rawResult": "not-applicable",
                    "evidenceRef": "N-A", "evidenceScope": "not-applicable",
                    "reason": "image-does-not-own-account-schema-migration"})
        supply_path = bundle / "image-supply.lock.yaml"
        supply_path.write_text(yaml.safe_dump(supply, sort_keys=False), encoding="utf-8")

        for relative in runner.REQUIRED_SEALED_FILES - {"profile.lock.yaml", "image-supply.lock.yaml"}:
            path = bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative in {"bootstrap-dag.yaml", "bootstrap.contract.yaml", "security/admission.yaml"}:
                shutil.copyfile(HERE / relative, path)
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
            for relative in (paths | runner.PLUGIN_SOURCE_PATHS[repository] if plugin else paths):
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
            "Application": "application",
            "CustomResourceDefinition": "customresourcedefinition",
            "Node": "node", "AnalysisTemplate": "analysistemplate",
        }
        def resource_spec(kind, name):
            if kind == "Application":
                return {"project": "fresh-account", "source": {"targetRevision": APPLICATION}}
            if kind == "Job":
                return {"template": {"metadata": {"labels": {"step": name}}}}
            if kind == "PersistentVolumeClaim":
                return {"accessModes": ["ReadWriteOnce"], "volumeName": "pvc-account-data"}
            if kind == "Node":
                return {"providerID": "kind://docker/" + name}
            if kind == "CustomResourceDefinition":
                return {"group": "argoproj.io", "names": {"kind": name.split(".", 1)[0]},
                        "scope": "Namespaced", "versions": [{"name": "v1alpha1",
                                                               "served": True,
                                                               "storage": True}]}
            if kind in {"Deployment", "StatefulSet", "Rollout"}:
                return {"replicas": 1, "template": {"metadata": {"labels": {"step": name}}}}
            return {"selector": {"app": name}}
        def command(kind, namespace, name):
            if kind == "Application":
                return {"tool": "argocd", "args": ["--core", "--kube-context", target_context,
                        "app", "get", name, "--output", "json"], "contentSha256": None}
            if kind == "Node":
                return {"tool": "kubectl", "args": ["--kubeconfig", kubeconfig,
                        "--context", target_context, "get", "node", name,
                        "--output", "json"], "contentSha256": None}
            if kind == "CustomResourceDefinition":
                return {"tool": "kubectl", "args": ["--kubeconfig", kubeconfig,
                        "--context", target_context, "get", "customresourcedefinition", name,
                        "--output", "json"], "contentSha256": None}
            return {"tool": "kubectl", "args": ["--kubeconfig", kubeconfig,
                    "--context", target_context, "get", resource_tokens[kind], name,
                    "--namespace", namespace, "--output", "json"], "contentSha256": None}
        def step_contract(step):
            if step in {"empty-node-cache", "image-import"}:
                contracts = [("v1", "Node", "None",
                              spec["target"]["clusterName"] + "-control-plane", "none",
                              "node-cache-empty" if step == "empty-node-cache"
                              else "node-images-present", {})]
            elif step == "crds":
                contracts = [
                    ("apiextensions.k8s.io/v1", "CustomResourceDefinition", "None", name,
                     "none", "conditions", {"Established": "True"})
                    for name in sorted({
                        "analysisruns.argoproj.io", "analysistemplates.argoproj.io",
                        "clusteranalysistemplates.argoproj.io", "experiments.argoproj.io",
                        "rollouts.argoproj.io",
                    })
                ]
            elif step == "controllers":
                contracts = [("apps/v1", "Deployment", "fresh-control", "argo-rollouts",
                              "observed-current", "workload-ready", {"Available": "True"})]
            elif step == "database":
                contracts = [
                    ("argoproj.io/v1alpha1", "Application", "fresh-control",
                     "fresh-account-release", "none", "argocd-operation-succeeded", {}),
                    ("v1", "PersistentVolumeClaim", "fresh-account", "account-data",
                     "none", "pvc-bound", {}),
                    ("v1", "Service", "fresh-account", "account-postgresql",
                     "none", "none", {}),
                    ("apps/v1", "StatefulSet", "fresh-account", "account-postgresql",
                     "observed-current", "statefulset-ready", {}),
                ]
            elif step == "application":
                contracts = [
                    ("argoproj.io/v1alpha1", "Application", "fresh-control",
                     "fresh-account-release", "none", "argocd-application-ready", {}),
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
                contracts = [
                    ("argoproj.io/v1alpha1", "Application", "fresh-control",
                     "fresh-account-release", "none", "argocd-operation-succeeded", {}),
                    ("batch/v1", "Job", "fresh-account", name,
                     "none", "job-complete", {"Complete": "True"}),
                ]
            else:
                if step == "acceptance":
                    contracts = [("argoproj.io/v1alpha1", "Rollout", "fresh-account",
                                  "account-api", "observed-current", "rollout-ready",
                                  {"Available": "True"})]
                else:
                    contracts = [("apps/v1", "Deployment", "fresh-control", step,
                                  "observed-current", "workload-ready", {"Available": "True"})]
            if plugin and step == "application":
                contracts.append(("argoproj.io/v1alpha1", "AnalysisTemplate", "fresh-account", "account-api-policy", "none", "none", {}))
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
                    "requiredImageDigests": ([item["ref"].rsplit("@", 1)[1]
                                              for item in spec["images"]
                                              if item["name"] != "kind-node"]
                                             if kind == "Node" else []),
                })
                metadata = {"name": name}
                if namespace != "None":
                    metadata["namespace"] = namespace
                manifests.append({"apiVersion": api_version, "kind": kind,
                                  "metadata": metadata, "spec": resource_spec(kind, name)})
            manifest_path = bundle / f"{step}-apply.json"
            manifest_path.write_text(json.dumps({"apiVersion": "v1", "kind": "List",
                                                  "items": manifests}), encoding="utf-8")
            manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            if step == "crds":
                manifest_digest = "9c4794e462b522f45077f0dabaf39df1ba3a35d29ec28787f6007ea989ddf07b"
            gitops_resources = {
                "database": [":PersistentVolumeClaim:account-data", ":Service:account-postgresql",
                             "apps:StatefulSet:account-postgresql"],
                "role-bootstrap": ["batch:Job:account-db-role-bootstrap"],
                "migration": ["batch:Job:account-migration"],
                "application": ["argoproj.io:Rollout:account-api", ":Service:account-api",
                                "networking.istio.io:DestinationRule:account-api",
                                "networking.istio.io:VirtualService:account-api"],
            }
            if step in gitops_resources:
                args = ["--core", "--kube-context", target_context,
                        "app", "sync", "fresh-account-release"]
                resources = gitops_resources[step]
                if plugin and step == "application": resources = ["argoproj.io:AnalysisTemplate:account-api-policy", *resources]
                for resource in resources:
                    args.extend(["--resource", resource])
                args.append("--prune=false")
                run = {"tool": "argocd", "args": args, "contentSha256": None}
            elif step == "cluster":
                run = {"tool": "kind", "args": ["create", "cluster", "--name",
                       spec["target"]["clusterName"], "--kubeconfig", kubeconfig, "--image",
                       profile_images["kind-node"]], "contentSha256": None}
            elif step == "image-import":
                archive = bundle / "images.oci.tar"
                archive.write_bytes(b"fixture-image-archive")
                run = {"tool": "kind", "args": ["load", "image-archive", str(archive.resolve()),
                       "--name", spec["target"]["clusterName"]],
                       "contentSha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
            elif step == "empty-node-cache":
                run = copy.deepcopy(verify[0])
            elif step == "controllers":
                chart = bundle / "argo-rollouts-2.43.7.fixture.tgz"
                chart.write_bytes(b"fixture-chart-bytes")
                run = {"tool": "helm", "args": ["--kubeconfig", kubeconfig,
                       "--kube-context", target_context, "upgrade", "--install", "argo-rollouts",
                       str(chart.resolve()), "--namespace", "fresh-control", "--version", "2.43.7",
                       "--set", "installCRDs=false",
                       "--set-string", "controller.image.tag=" +
                       adapter_contract.ROLLOUTS_CONTROLLER_TAG,
                       "--atomic", "--wait", "--wait-for-jobs", "--create-namespace"],
                       "contentSha256": "166506174f4be91ed2758fccbca340e209cba35aa72ac08b5863a7b797942487"}
            elif step == "acceptance":
                run = {"tool": "rollouts", "args": ["--kubeconfig", kubeconfig,
                       "--context", target_context, "restart", "rollout", "account-api",
                       "--namespace", "fresh-account"], "contentSha256": None}
            else:
                run = {"tool": "kubectl", "args": ["--kubeconfig", kubeconfig,
                       "--context", target_context, "apply", "--filename",
                       str(manifest_path.resolve())], "contentSha256": manifest_digest}
            reconcile = copy.deepcopy(verify)
            if step == "crds":
                for read in reconcile:
                    read["args"].insert(-2, "--ignore-not-found=true")
            return {"phase": "cluster-create" if step == "cluster" else "post-cluster",
                    "verify": verify, "reconcile": reconcile, "run": [run],
                    "resourceIdentities": identities, "observations": observations,
                    "hostArtifacts": []}
        preflight_command = {"tool": "kind", "args": ["get", "clusters"],
                             "contentSha256": None}
        preflight = {"phase": "pre-cluster", "verify": [preflight_command],
                     "reconcile": [copy.deepcopy(preflight_command)],
                     "run": [copy.deepcopy(preflight_command)],
                     "resourceIdentities": ["HostState/None/target-cluster-absent"],
                     "observations": [], "hostArtifacts": []}
        adapter_contract = runner._load_contract_module("approved_live_adapter")
        supply_contract = adapter_contract.supply_plan_contract(supply, spec)
        host_artifacts = []
        for artifact_id, contract_item in sorted(supply_contract["hostArtifacts"].items()):
            image_name = artifact_id.removesuffix("-archive")
            path = archive_paths[image_name]
            host_artifacts.append({
                "id": artifact_id, "path": str(path.resolve()),
                "sha256": contract_item["sha256"], "sizeBytes": path.stat().st_size,
                "sourceIdentity": contract_item["sourceIdentity"],
            })
        image_source = {"phase": "pre-cluster", "verify": [], "reconcile": [], "run": [],
                        "resourceIdentities": ["HostArtifact/None/" + item["id"]
                                               for item in host_artifacts],
                        "observations": [], "hostArtifacts": host_artifacts}
        steps = {step: step_contract(step) for step in runner.STEPS
                 if step not in {"preflight", "image-source"}}
        steps = {"preflight": preflight, "image-source": image_source, **steps}
        if plugin:
            action=steps["controllers"]
            action["resourceIdentities"]=[]; action["observations"]=[]; action["verify"]=[]
            for obj in objects:
                kind, ns, name = obj["kind"], obj["metadata"]["namespace"], obj["metadata"]["name"]
                identity=f"{kind}/{ns}/{name}"
                pointer="/spec" if kind=="Deployment" else ("/data" if kind=="ConfigMap" else ("/rules" if kind=="Role" else "/subjects"))
                desired={pointer:adapter_contract._json_pointer(obj,pointer)}
                action["resourceIdentities"].append(identity)
                action["observations"].append({"apiVersion":obj["apiVersion"],"identity":identity,"desiredPaths":[pointer],"desiredSha256":adapter_contract._mapping_digest(desired),"generationPolicy":"observed-current" if kind=="Deployment" else "none","healthPolicy":"workload-ready" if kind=="Deployment" else "none","requiredConditions":{"Available":"True"} if kind=="Deployment" else {},"requiredImageDigests":[]})
                token={"Deployment":"deployment","ConfigMap":"configmap","Role":"role","RoleBinding":"rolebinding"}[kind]
                action["verify"].append({"tool":"kubectl","args":["--kubeconfig",kubeconfig,"--context",target_context,"get",token,name,"--namespace",ns,"--output","json"],"contentSha256":None})
            action["reconcile"]=copy.deepcopy(action["verify"])
            action["run"].append({"tool":"kubectl","args":["--kubeconfig",kubeconfig,"--context",target_context,"apply","--filename",str(plugin_path.resolve())],"contentSha256":plugin_contract["bundleSha256"]})
        adapter_plan = {
            "schemaVersion": "platform.lab/approved-live-step-plan/v3",
            "target": {"clusterName": spec["target"]["clusterName"],
                       "context": target_context, "kubeconfig": kubeconfig},
            "tools": tools,
            "identity": {"clusterUID": {"tool": "kubectl", "args": [
                "--kubeconfig", kubeconfig, "--context", target_context,
                "get", "namespace", "kube-system", "--output",
                "jsonpath={.metadata.uid}"], "contentSha256": None}},
            "steps": steps,
        }
        if plugin:
            adapter_plan["rolloutsPlugin"] = plugin_contract
        adapter_plan_path.write_text(json.dumps(adapter_plan) + "\n", encoding="utf-8")
        FakeExecutor.plan_sha256 = hashlib.sha256(adapter_plan_path.read_bytes()).hexdigest()
        adapter_input_set_digest = adapter_contract.plan_input_set_digest(adapter_plan)
        approval = {
            "apiVersion": "platform.lab/v1alpha1", "kind": "FreshExecutionApproval",
            "metadata": {"name": "account-lightweight-fresh", "approvedBy": "fixture-reviewer",
                         "approvedAt": "2026-10-25T11:00:00Z", "expiresAt": "2026-10-25T13:00:00Z"},
            "spec": {
                "approved": True,
                "target": {"clusterName": spec["target"]["clusterName"],
                           "context": spec["target"]["context"],
                           "profileName": "account-lightweight-fresh"},
                "repositories": {"platformGitops": PLATFORM, "applicationManifest": APPLICATION},
                "hashes": {"profileSha256": profile_digest,
                           "materializationSha256": materialization_digest,
                           "sourceSetSha256": runner._mapping_digest(source_files),
                           "liveAdapterPlanSha256": hashlib.sha256(adapter_plan_path.read_bytes()).hexdigest(),
                           "liveAdapterInputSetSha256": adapter_input_set_digest},
                "scope": copy.deepcopy(runner.SCOPE),
                "gates": {gate: {"approved": True, "approvalId": f"fixture-{gate}"}
                          for gate in runner.GATES},
                "inputDigests": {},
                "allowedSteps": list(runner.STEPS),
            },
        }
        references = {spec["inputs"]["clusterKubeconfigRef"], spec["inputs"]["imageSupplyRef"]}
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

    def test_approval_schema_allows_only_controller_prefix_or_full_dag(self):
        schema = json.loads((HERE / "approval.schema.json").read_text(encoding="utf-8"))
        approval = json.loads((HERE / "approval.template.json").read_text(encoding="utf-8"))
        self.assertEqual(list(Draft7Validator(schema).iter_errors(approval)), [])
        approval["spec"]["allowedSteps"] = list(runner.CONTROLLER_STAGE_STEPS)
        self.assertEqual(list(Draft7Validator(schema).iter_errors(approval)), [])
        approval["spec"]["allowedSteps"] = list(runner.STEPS[:4])
        self.assertTrue(list(Draft7Validator(schema).iter_errors(approval)))
        approval["spec"]["allowedSteps"] = list(runner.STEPS)
        approval["spec"]["gates"]["G3"] = approval["spec"]["gates"].pop(
            "G3Precondition")
        self.assertTrue(list(Draft7Validator(schema).iter_errors(approval)))

    def test_kind_node_is_cluster_provenance_not_a_node_import_digest(self):
        (_, _, materialization_path, _, _, _, _) = self.fixture()
        supply = yaml.safe_load((materialization_path.parent /
                                 "image-supply.lock.yaml").read_text(encoding="utf-8"))
        by_name = {item["name"]: item for item in supply["spec"]["images"]}
        kind_digest = by_name["kind-node"]["source"]["manifest"]["manifestDigest"]
        expected = {
            item["source"]["manifest"]["manifestDigest"]
            for item in supply["spec"]["images"] if item["name"] != "kind-node"
        }
        self.assertEqual(runner._node_import_image_digests(supply), expected)
        self.assertNotIn(kind_digest, expected)

        without_kind = copy.deepcopy(supply)
        without_kind["spec"]["images"] = [
            item for item in without_kind["spec"]["images"]
            if item["name"] != "kind-node"
        ]
        with self.assertRaisesRegex(runner.RunnerRejected, "image-supply-kind-node"):
            runner._node_import_image_digests(without_kind)

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

    def test_full_plan_rejects_adapter_without_g3_runtime_proof_before_executor(self):
        (_, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        executor = FakeExecutor()
        executor.supports_credential_runtime_proof = False
        with self.assertRaisesRegex(runner.RunnerRejected,
                                    "adapter-g3-runtime-proof-unsupported"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source,
                           now=NOW)
        self.assertEqual(executor.identity_calls, [])
        self.assertEqual(executor.verify_calls, [])
        self.assertEqual(executor.reconcile_calls, [])
        self.assertEqual(executor.run_calls, [])
        self.assertFalse(journal_path.exists())

    def test_approval_and_journal_bind_the_complete_live_input_byte_set(self):
        (approval, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        approval["spec"]["hashes"]["liveAdapterInputSetSha256"] = "0" * 64
        self.rewrite_approval(approval_path, approval)
        executor = FakeExecutor()
        with self.assertRaisesRegex(runner.RunnerRejected,
                                    "approval-live-adapter-input-set-hash"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source,
                           now=NOW)
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

    def test_controller_stage_needs_g3_precondition_but_not_runtime_proof(self):
        (approval, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        approval["spec"]["allowedSteps"] = list(runner.CONTROLLER_STAGE_STEPS)
        self.rewrite_approval(approval_path, approval)
        executor = FakeExecutor(credential_proof=False)
        result = runner.execute("first", approval_path, materialization_path, profile_path,
                                journal_path, executor, platform_source, application_source,
                                now=NOW)
        self.assertEqual(executor.run_calls, list(runner.CONTROLLER_STAGE_STEPS))
        self.assertEqual(result["steps"]["controllers"]["status"], "succeeded")
        self.assertEqual(result["steps"]["credential-bootstrap"]["status"], "pending")

    def test_controller_stage_rejects_unapproved_g3_precondition_before_executor(self):
        (approval, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        profile["spec"]["decisions"]["G3Precondition"]["status"] = "pending-approval"
        profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
        self.reseal(approval, approval_path, materialization_path, profile_path)
        approval["spec"]["allowedSteps"] = list(runner.CONTROLLER_STAGE_STEPS)
        self.rewrite_approval(approval_path, approval)
        executor = FakeExecutor()
        with self.assertRaisesRegex(runner.RunnerRejected,
                                    "profile-pending-input|profile-contract"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source,
                           now=NOW)
        self.assertEqual(executor.identity_calls, [])
        self.assertEqual(executor.run_calls, [])

    def test_expired_g3_precondition_stops_controller_stage_before_executor(self):
        (approval, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        plan = profile["spec"]["inputs"]["credentialIssuancePlan"]
        for item in [*plan["secrets"], plan["ca"]]:
            item["expiry"] = "2026-10-24"
        profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
        self.reseal(approval, approval_path, materialization_path, profile_path)
        approval["spec"]["allowedSteps"] = list(runner.CONTROLLER_STAGE_STEPS)
        self.rewrite_approval(approval_path, approval)
        executor = FakeExecutor()
        with self.assertRaisesRegex(runner.RunnerRejected, "g3-precondition-expired"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source,
                           now=NOW)
        self.assertEqual(executor.identity_calls, [])

    def test_only_fixed_controller_prefix_or_full_plan_can_be_approved(self):
        (approval, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        approval["spec"]["allowedSteps"] = list(runner.STEPS[:4])
        self.rewrite_approval(approval_path, approval)
        executor = FakeExecutor()
        with self.assertRaisesRegex(runner.RunnerRejected, "approval-steps"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source,
                           now=NOW)
        self.assertEqual(executor.identity_calls, [])

    def test_full_plan_stops_at_credential_step_without_runtime_proof(self):
        (_, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        executor = FakeExecutor(credential_proof=False)
        with self.assertRaisesRegex(runner.RunnerRejected, "g3-runtime-proof"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source,
                           now=NOW)
        self.assertIn("credential-bootstrap", executor.run_calls)
        self.assertNotIn("custom-resources", executor.run_calls)
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["steps"]["credential-bootstrap"]["status"], "uncertain")

        resumed = FakeExecutor(cluster_uid="fixture-cluster-uid",
                               credential_proof=False)
        with self.assertRaisesRegex(runner.RunnerRejected,
                                    "uncertain-job-outcome:credential-bootstrap"):
            runner.execute("resume", approval_path, materialization_path, profile_path,
                           journal_path, resumed, platform_source, application_source,
                           now=NOW)
        self.assertNotIn("credential-bootstrap", resumed.run_calls)

    def test_controller_stage_can_resume_under_new_full_approval_with_g3_runtime_proof(self):
        (approval, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        approval["spec"]["allowedSteps"] = list(runner.CONTROLLER_STAGE_STEPS)
        self.rewrite_approval(approval_path, approval)
        first = FakeExecutor()
        runner.execute("first", approval_path, materialization_path, profile_path,
                       journal_path, first, platform_source, application_source, now=NOW)

        approval["spec"]["allowedSteps"] = list(runner.STEPS)
        approval["metadata"].update(approvedAt="2026-10-25T11:30:00Z",
                                    expiresAt="2026-10-25T13:30:00Z")
        self.rewrite_approval(approval_path, approval)
        resumed = FakeExecutor(cluster_uid="fixture-cluster-uid")
        result = runner.execute("resume", approval_path, materialization_path, profile_path,
                                journal_path, resumed, platform_source, application_source,
                                now=NOW)
        self.assertEqual(resumed.run_calls, list(runner.STEPS[len(
            runner.CONTROLLER_STAGE_STEPS):]))
        self.assertEqual(result["steps"]["acceptance"]["status"], "succeeded")

    def test_wrong_runtime_proof_identity_stops_before_custom_resources(self):
        (_, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        executor = FakeExecutor()
        original = executor.credential_proof

        def wrong_proof():
            proof = original()
            proof["secrets"][0]["uid"] = ""
            return proof

        executor.credential_proof = wrong_proof
        with self.assertRaisesRegex(runner.RunnerRejected,
                                    "g3-runtime-proof-secret-identity"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source,
                           now=NOW)
        self.assertNotIn("custom-resources", executor.run_calls)

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

    def test_unneeded_repository_credential_plan_target_is_rejected(self):
        (approval, approval_path, materialization_path, profile_path, journal_path,
         platform_source, application_source) = self.fixture()
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        profile["spec"]["inputs"]["credentialIssuancePlan"]["secrets"].append({
            "name": "repository-read", "purpose": "unneeded public Git credential",
            "destination": "fresh-control/repository-read", "owner": "runtime-owner",
            "expiry": "2026-11-24", "secretType": "Opaque", "keyNames": ["token"],
            "issuerRef": "issuer:fixture", "cleanupOwner": "fixture-cleanup",
        })
        profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
        self.reseal(approval, approval_path, materialization_path, profile_path)
        executor = FakeExecutor()
        with self.assertRaisesRegex(runner.RunnerRejected,
                                    "profile-contract:g3-precondition-secret-set"):
            runner.execute("first", approval_path, materialization_path, profile_path,
                           journal_path, executor, platform_source, application_source, now=NOW)
        self.assertEqual(executor.identity_calls, [])
        self.assertEqual(executor.run_calls, [])

    def test_existing_upgrade_cli_reaches_real_sealed_gate_and_rejects_tampering(self):
        # The fixture's chart and CRD hash shims in setUp are retained; the
        # live gate itself and all supply/source validations are real.
        import contextlib, io, sys
        upgrade_spec=importlib.util.spec_from_file_location("synthetic_upgrade_fixture",HERE/"test_existing_upgrade.py")
        upgrade_tests=importlib.util.module_from_spec(upgrade_spec);upgrade_spec.loader.exec_module(upgrade_tests)
        upgrade_fixture=upgrade_tests.UpgradeTests("test_live_adapter_is_mutation_disabled")
        upgrade_fixture.setUp();self.addCleanup(upgrade_fixture.doCleanups)
        approval, gate_approval, materialization_path, profile_path, journal, platform, application = self.fixture(plugin=True)
        materialization=json.loads(materialization_path.read_text())
        for relative in runner.PLUGIN_SOURCE_PATHS["platform-gitops"]:
            source=platform/relative;source.write_bytes((HERE.parent.parent/relative).read_bytes())
            identity=f"platform-gitops@{PLATFORM}:{relative}"
            materialization["sourceFiles"][identity]=hashlib.sha256(source.read_bytes()).hexdigest()
        materialization_path.write_text(json.dumps(materialization,sort_keys=True))
        approval["metadata"].update(approvedAt="2026-10-25T00:00:00Z",expiresAt="2026-11-23T00:00:00Z")
        self.reseal(approval,gate_approval,materialization_path,profile_path)
        upgrade=upgrade_fixture.plan
        upgrade["target"].update(clusterName="learning-fresh-fixture-sandbox",context="kind-learning-fresh-fixture-sandbox")
        upgrade["source"].update(platformRevision=PLATFORM,newApplicationRevision=APPLICATION)
        upgrade["gitDesired"]["sourceRender"]["platformRevision"]=PLATFORM
        upgrade["gitDesired"]["root"]["revision"]=PLATFORM
        upgrade["gitDesired"]["sourceRender"]["prePause"]["platformRevision"]=PLATFORM
        upgrade["gitDesired"]["sourceRender"]["transitions"]["rollbackOld"]["platformRevision"]=PLATFORM
        upgrade["gitDesired"]["child"]["newRevision"]=APPLICATION
        upgrade["gitDesired"]["sourceRender"]["newWorkloadRevision"]=APPLICATION
        plugin=json.loads((materialization_path.parent/"live-step-plan.json").read_text())["rolloutsPlugin"]
        bundle=materialization_path.parent/"plugin-bundle.json"
        upgrade["artifacts"].update(controllerImageDigest=plugin["controllerImage"].split("@",1)[1],binarySha256=plugin["binarySha256"],bundleSha256=plugin["bundleSha256"],bundlePath=str(bundle))
        upgrade_fixture.plan_path.write_text(json.dumps(upgrade))
        upgrade_fixture.approval.update(approvedAt="2026-10-25T00:00:00Z",expiresAt="2026-11-23T00:00:00Z",planSha256=hashlib.sha256(upgrade_fixture.plan_path.read_bytes()).hexdigest())
        upgrade_fixture.approval_path.write_text(json.dumps(upgrade_fixture.approval))
        argv=["runner.py","--mode","existing-upgrade","--upgrade-action","first","--live-plan",str(upgrade_fixture.plan_path),"--approval",str(upgrade_fixture.approval_path),"--journal",str(journal),"--gate-approval",str(gate_approval),"--gate-plan",str(materialization_path.parent/"live-step-plan.json"),"--materialization",str(materialization_path),"--profile",str(profile_path),"--platform-source-root",str(platform),"--application-source-root",str(application)]
        def main_output():
            output=io.StringIO()
            with mock.patch.object(sys,"argv",argv),contextlib.redirect_stdout(output):
                result=runner.main()
            return result,output.getvalue()
        result,output=main_output()
        self.assertEqual(result,2);self.assertIn("upgrade-mutation-capability-unavailable",output)
        self.assertFalse(journal.exists())
        approval["spec"]["approved"]=False;self.rewrite_approval(gate_approval,approval)
        result,output=main_output();self.assertEqual(result,2);self.assertIn("approval-not-granted",output)
        approval["spec"]["approved"]=True;self.rewrite_approval(gate_approval,approval)
        (materialization_path.parent/"image-supply.lock.yaml").write_text("tampered")
        result,output=main_output();self.assertEqual(result,2);self.assertIn("materialization-file-digest",output)

    def test_reviewed_plugin_through_runner_entry_and_tamper_rejection(self):
        from types import SimpleNamespace
        class Transport:
            def __init__(self): self.calls=[]
            def run(self, argv, env):
                self.calls.append(argv)
                return SimpleNamespace(returncode=0,stdout="fixture applied",stderr="")
        approval, approval_path, materialization_path, profile_path, _, platform, application = self.fixture(plugin=True)
        plan_path = materialization_path.parent / "live-step-plan.json"
        approval["spec"]["allowedSteps"] = list(runner.CONTROLLER_STAGE_STEPS)
        self.rewrite_approval(approval_path, approval)
        fake = Transport()
        live = runner.create_approved_adapter(approval_path,materialization_path,profile_path,platform,application,plan_path,transport=fake,now=NOW)
        self.assertEqual(live.plugin_contract["sourceRevision"],APPLICATION)
        plugin_command=live.plan["steps"]["controllers"]["run"][-1]
        _, complete = live._run_commands([plugin_command])
        self.assertTrue(complete)
        self.assertEqual(fake.calls[-1][-3:-1],["apply","--filename"])
        # Drive the normal runner lifecycle with synthetic step observations.
        delegate = FakeExecutor()
        live.identity, live.run, live.verify, live.reconcile = delegate.identity, delegate.run, delegate.verify, delegate.reconcile
        journal = materialization_path.parent.parent / "plugin-journal.json"
        runner.execute("first", approval_path, materialization_path, profile_path, journal, live, platform, application, plan_path, now=NOW)
        self.assertEqual(delegate.run_calls, list(runner.CONTROLLER_STAGE_STEPS))
        # Approval denies execution even if all other synthetic evidence matches.
        denied=copy.deepcopy(approval); denied["spec"]["approved"]=False
        self.rewrite_approval(approval_path,denied)
        with self.assertRaisesRegex(runner.RunnerRejected,"approval-not-granted"):
            runner.create_approved_adapter(approval_path,materialization_path,profile_path,platform,application,plan_path,transport=fake,now=NOW)
        self.rewrite_approval(approval_path,approval)
        # Sealed source bytes are checked even when the revision label is unchanged.
        plugin_source=application/"learning/account-release/metric-plugin/fresh_query.go"
        saved=plugin_source.read_bytes(); plugin_source.write_bytes(b"tampered")
        before=len(fake.calls)
        with self.assertRaisesRegex(runner.RunnerRejected,"source-file-digest"):
            runner.create_approved_adapter(approval_path,materialization_path,profile_path,platform,application,plan_path,transport=fake,now=NOW)
        self.assertEqual(len(fake.calls),before)
        plugin_source.write_bytes(saved)
        # An altered bundle is caught before any transport call.
        bundle=materialization_path.parent/"plugin-bundle.json"
        bundle.write_text("{}",encoding="utf-8")
        before=len(fake.calls)
        with self.assertRaises(runner.RunnerRejected):
            runner.create_approved_adapter(approval_path,materialization_path,profile_path,platform,application,plan_path,transport=fake,now=NOW)
        self.assertEqual(len(fake.calls),before)

    def test_plugin_source_and_image_must_match_sealed_supply(self):
        for field in ("sourceRevision","controllerImage","binarySha256","bundleSha256"):
            with self.subTest(field=field):
                approval, approval_path, materialization_path, profile_path, _, platform, application=self.fixture(plugin=True)
                plan_path=materialization_path.parent/"live-step-plan.json"
                plan=json.loads(plan_path.read_text())
                plan["rolloutsPlugin"][field] = ("d" * 40 if field=="sourceRevision" else ("local/other@sha256:"+"d"*64 if field=="controllerImage" else "d"*64))
                plan_path.write_text(json.dumps(plan),encoding="utf-8")
                approval["spec"]["hashes"]["liveAdapterPlanSha256"]=hashlib.sha256(plan_path.read_bytes()).hexdigest()
                self.rewrite_approval(approval_path,approval)
                with self.assertRaises(runner.RunnerRejected):
                    runner.create_approved_adapter(approval_path,materialization_path,profile_path,platform,application,plan_path,now=NOW)

    def test_plugin_supply_must_be_approved_and_complete(self):
        for change in ("missing", "unapproved", "source"):
            with self.subTest(change=change):
                approval, approval_path, materialization_path, profile_path, _, platform, application = self.fixture(plugin=True)
                supply_path=materialization_path.parent/"image-supply.lock.yaml"
                supply=yaml.safe_load(supply_path.read_text())
                if change=="missing": supply["spec"].pop("rolloutsPlugin")
                if change=="unapproved": supply["spec"]["decision"]["status"]="pending-approval"
                if change=="source": supply["spec"]["rolloutsPlugin"]["sourceRevision"]="d"*40
                supply_path.write_text(yaml.safe_dump(supply),encoding="utf-8")
                self.reseal(approval,approval_path,materialization_path,profile_path)
                with self.assertRaises(runner.RunnerRejected):
                    runner.create_approved_adapter(approval_path,materialization_path,profile_path,platform,application,materialization_path.parent/"live-step-plan.json",now=NOW)


if __name__ == "__main__":
    unittest.main(verbosity=2)

