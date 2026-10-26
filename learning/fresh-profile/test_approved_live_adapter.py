#!/usr/bin/env python3
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from jsonschema import Draft7Validator


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("approved_live_adapter", HERE / "approved_live_adapter.py")
adapter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(adapter)


class FakeTransport:
    def __init__(self, identity_missing=False, observation_mutation=None,
                 observation_stdout=None, missing_crds=None, crd_error=None,
                 crd_returncode=1):
        self.calls = []
        self.environments = []
        self.identity_missing = identity_missing
        self.observation_mutation = observation_mutation
        self.observation_stdout = observation_stdout
        self.missing_crds = set(missing_crds or ())
        self.crd_error = crd_error
        self.crd_returncode = crd_returncode
        self.images_loaded = False

    def run(self, argv, env):
        self.calls.append(argv)
        self.environments.append(env)
        if "jsonpath={.metadata.uid}" in argv:
            if self.identity_missing:
                return SimpleNamespace(returncode=1, stdout="", stderr="context absent")
            stdout = "fixture-cluster-uid\n"
        elif argv[-2:] == ["get", "clusters"]:
            stdout = ""
        elif "apply" in argv or "sync" in argv or "create" in argv or "load" in argv \
                or "install" in argv or "upgrade" in argv or "restart" in argv:
            if "load" in argv:
                self.images_loaded = True
            stdout = "applied\n"
        else:
            if ("get" in argv and "customresourcedefinition" in argv
                    and self.crd_error is not None):
                return SimpleNamespace(returncode=self.crd_returncode, stdout="",
                                       stderr=self.crd_error)
            if ("get" in argv and "customresourcedefinition" in argv
                    and argv[argv.index("customresourcedefinition") + 1] in self.missing_crds):
                if "--ignore-not-found=true" in argv:
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="NotFound")
            if self.observation_stdout is not None:
                stdout = self.observation_stdout
            else:
                get_index = argv.index("get")
                if argv[get_index - 1] == "app":
                    resource_kind, name = "application", argv[get_index + 1]
                    api_version, kind, namespace = "argoproj.io/v1alpha1", "Application", "fresh-control"
                else:
                    resource_kind, name = argv[get_index + 1:get_index + 3]
                    api_version, kind = adapter.KUBECTL_RESOURCES[resource_kind]
                    namespace = ("None" if kind in {"Node", "CustomResourceDefinition"}
                                 else argv[argv.index("--namespace") + 1])
                spec = ApprovedLiveAdapterTests.resource_spec(kind, name)
                status = ApprovedLiveAdapterTests.resource_status(
                    kind, images_loaded=self.images_loaded)
                metadata = {"name": name, "uid": "uid-" + name, "generation": 2}
                if namespace != "None":
                    metadata["namespace"] = namespace
                value = {
                    "apiVersion": api_version, "kind": kind,
                    "metadata": metadata,
                    "spec": spec, "status": status,
                }
                if self.observation_mutation is not None:
                    self.observation_mutation(value)
                stdout = json.dumps(value)
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


class ApprovedLiveAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(tempfile.mkdtemp(prefix="live-adapter-test-", dir=HERE))
        self.addCleanup(shutil.rmtree, self.temporary, True)
        self.context = "kind-learning-fresh-fixture-sandbox"
        self.cluster = "learning-fresh-fixture-sandbox"
        self.kubeconfig = str((self.temporary / "fresh.kubeconfig").resolve())
        self.versions = {
            "kind": "v0.33.0", "kubectl": "v1.36.4", "helm": "v4.2.4",
            "istioctl": "1.31.2", "argocd": "v3.5.2", "rollouts": "v1.10.1",
        }
        original_sha256 = adapter._sha256
        original_content_sha256 = adapter._content_sha256
        self.real_sha256 = original_sha256

        def fixture_sha256(path):
            if Path(path).name == "crds.json":
                return adapter.ROLLOUTS_CRD_LIST_SHA256
            return original_sha256(path)

        patcher = mock.patch.object(adapter, "_sha256", side_effect=fixture_sha256)
        patcher.start()
        self.addCleanup(patcher.stop)

        def fixture_content_sha256(path):
            if Path(path).name == "chart":
                return adapter.ROLLOUTS_CHART_SHA256
            return original_content_sha256(path)

        content_patcher = mock.patch.object(
            adapter, "_content_sha256", side_effect=fixture_content_sha256)
        content_patcher.start()
        self.addCleanup(content_patcher.stop)

    RESOURCE_TOKEN = {
        "Deployment": "deployment", "Job": "job", "PersistentVolumeClaim": "pvc",
        "StatefulSet": "statefulset", "Rollout": "rollout", "Service": "service",
        "DestinationRule": "destinationrule", "VirtualService": "virtualservice",
        "Application": "application", "CustomResourceDefinition": "customresourcedefinition",
    }

    @staticmethod
    def resource_spec(kind, name):
        if kind == "Application":
            return {"project": "fresh-account", "source": {"targetRevision": "9" * 40}}
        if kind == "Job":
            return {"template": {"metadata": {"labels": {"platform.lab/step": name}}}}
        if kind == "PersistentVolumeClaim":
            return {"accessModes": ["ReadWriteOnce"], "volumeName": "pvc-account-data"}
        if kind == "Node":
            return {"providerID": "kind://docker/" + name}
        if kind == "CustomResourceDefinition":
            return {"group": "argoproj.io", "names": {"kind": name.split(".", 1)[0]},
                    "scope": "Namespaced", "versions": [{"name": "v1alpha1", "served": True,
                                                           "storage": True}]}
        if kind in {"Deployment", "StatefulSet", "Rollout"}:
            return {"replicas": 1, "template": {"metadata":
                    {"labels": {"platform.lab/step": name}}}}
        return {"selector": {"app": name}}

    @staticmethod
    def resource_status(kind, images_loaded=False):
        if kind == "Application":
            return {"operationState": {"phase": "Succeeded",
                    "finishedAt": "2026-10-25T13:00:00Z",
                    "syncResult": {"revision": "9" * 40}},
                    "sync": {"status": "Synced"}, "health": {"status": "Healthy"},
                    "conditions": []}
        if kind == "Node":
            names = (["fixture/image@sha256:" + "d" * 64] if images_loaded else [])
            return {"images": [{"names": names}] if names else [], "conditions": []}
        if kind == "CustomResourceDefinition":
            return {"conditions": [{"type": "Established", "status": "True"}]}
        if kind == "Job":
            return {"succeeded": 1, "completionTime": "2026-10-25T13:00:00Z",
                    "conditions": [{"type": "Complete", "status": "True"}]}
        if kind == "PersistentVolumeClaim":
            return {"phase": "Bound", "capacity": {"storage": "1Gi"}, "conditions": []}
        if kind == "StatefulSet":
            return {"observedGeneration": 2, "replicas": 1, "readyReplicas": 1,
                    "currentReplicas": 1, "updatedReplicas": 1,
                    "currentRevision": "account-1", "updateRevision": "account-1",
                    "conditions": []}
        if kind == "Rollout":
            return {"observedGeneration": 2, "replicas": 1, "readyReplicas": 1,
                    "availableReplicas": 1, "updatedReplicas": 1,
                    "currentPodHash": "api-1", "stableRS": "api-1",
                    "conditions": [{"type": "Available", "status": "True"}]}
        if kind == "Deployment":
            return {"observedGeneration": 2, "readyReplicas": 1,
                    "availableReplicas": 1,
                    "conditions": [{"type": "Available", "status": "True"}]}
        return {"conditions": []}

    def get_command(self, kind, namespace, name):
        if kind == "Application":
            return {"tool": "argocd", "args": ["--core", "--kube-context", self.context,
                    "app", "get", name, "--output", "json"], "contentSha256": None}
        if kind == "Node":
            return {"tool": "kubectl", "args": ["--kubeconfig", self.kubeconfig,
                    "--context", self.context, "get", "node", name,
                    "--output", "json"], "contentSha256": None}
        if kind == "CustomResourceDefinition":
            return {"tool": "kubectl", "args": ["--kubeconfig", self.kubeconfig,
                    "--context", self.context, "get", "customresourcedefinition", name,
                    "--output", "json"], "contentSha256": None}
        return {"tool": "kubectl", "args": ["--kubeconfig", self.kubeconfig,
                "--context", self.context, "get", self.RESOURCE_TOKEN[kind], name,
                "--namespace", namespace, "--output", "json"], "contentSha256": None}

    @staticmethod
    def desired_digest(name, kind="Deployment"):
        spec = ApprovedLiveAdapterTests.resource_spec(kind, name)
        desired = {"/spec": spec}
        return hashlib.sha256(json.dumps(desired, sort_keys=True,
                              separators=(",", ":")).encode()).hexdigest()

    def plan(self):
        tools = {}
        for alias, version in self.versions.items():
            path = self.temporary / (alias + ".fixture")
            path.write_text(alias + ":" + version + "\n", encoding="utf-8")
            tools[alias] = {"path": str(path.resolve()),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "version": version}
        def resource_contracts(step):
            if step in {"empty-node-cache", "image-import"}:
                return [("v1", "Node", "None", self.cluster + "-control-plane",
                         "none", "node-cache-empty" if step == "empty-node-cache"
                         else "node-images-present", {})]
            if step == "crds":
                return [
                    ("apiextensions.k8s.io/v1", "CustomResourceDefinition", "None", name,
                     "none", "conditions", {"Established": "True"})
                    for name in sorted({
                        "analysisruns.argoproj.io", "analysistemplates.argoproj.io",
                        "clusteranalysistemplates.argoproj.io", "experiments.argoproj.io",
                        "rollouts.argoproj.io",
                    })
                ]
            if step == "controllers":
                return [("apps/v1", "Deployment", "fresh-control", "argo-rollouts",
                         "observed-current", "workload-ready", {"Available": "True"})]
            if step == "database":
                return [
                    ("argoproj.io/v1alpha1", "Application", "fresh-control", "fresh-account-release",
                     "none", "argocd-operation-succeeded", {}),
                    ("v1", "PersistentVolumeClaim", "fresh-account", "account-data",
                     "none", "pvc-bound", {}),
                    ("v1", "Service", "fresh-account", "account-postgresql",
                     "none", "none", {}),
                    ("apps/v1", "StatefulSet", "fresh-account", "account-postgresql",
                     "observed-current", "statefulset-ready", {}),
                ]
            if step in {"role-bootstrap", "migration"}:
                name = "account-db-role-bootstrap" if step == "role-bootstrap" else "account-migration"
                return [
                    ("argoproj.io/v1alpha1", "Application", "fresh-control", "fresh-account-release",
                     "none", "argocd-operation-succeeded", {}),
                    ("batch/v1", "Job", "fresh-account", name,
                     "none", "job-complete", {"Complete": "True"}),
                ]
            if step == "application":
                return [
                    ("argoproj.io/v1alpha1", "Application", "fresh-control", "fresh-account-release",
                     "none", "argocd-application-ready", {}),
                    ("argoproj.io/v1alpha1", "Rollout", "fresh-account", "account-api",
                     "observed-current", "rollout-ready", {"Available": "True"}),
                    ("v1", "Service", "fresh-account", "account-api",
                     "none", "none", {}),
                    ("networking.istio.io/v1beta1", "DestinationRule", "fresh-account",
                     "account-api", "none", "none", {}),
                    ("networking.istio.io/v1beta1", "VirtualService", "fresh-account",
                     "account-api", "none", "none", {}),
                ]
            kind = "Rollout" if step == "acceptance" else "Deployment"
            api_version = "argoproj.io/v1alpha1" if kind == "Rollout" else "apps/v1"
            name = "account-api" if step == "acceptance" else step
            namespace = "fresh-account" if step == "acceptance" else "fresh-control"
            return [(api_version, kind, namespace, name, "observed-current",
                     "rollout-ready" if kind == "Rollout" else "workload-ready",
                     {"Available": "True"})]

        def actions(step):
            contracts = resource_contracts(step)
            observations = []
            manifests = []
            verify = []
            identities = []
            for api_version, kind, namespace, name, generation, health, conditions in contracts:
                identity = f"{kind}/{namespace}/{name}"
                identities.append(identity)
                verify.append(self.get_command(kind, namespace, name))
                spec = self.resource_spec(kind, name)
                observations.append({
                    "apiVersion": api_version,
                    "identity": identity,
                    "desiredPaths": ["/spec"],
                    "desiredSha256": self.desired_digest(name, kind),
                    "generationPolicy": generation,
                    "healthPolicy": health,
                    "requiredConditions": conditions,
                    "requiredImageDigests": (["sha256:" + "d" * 64]
                                             if kind == "Node" else []),
                })
                metadata = {"name": name}
                if namespace != "None":
                    metadata["namespace"] = namespace
                manifests.append({"apiVersion": api_version, "kind": kind,
                                  "metadata": metadata, "spec": spec})
            manifest = self.temporary / f"{step}.json"
            manifest.write_text(json.dumps({"apiVersion": "v1", "kind": "List",
                                             "items": manifests}), encoding="utf-8")
            content = hashlib.sha256(manifest.read_bytes()).hexdigest()
            if step == "crds":
                content = adapter.ROLLOUTS_CRD_LIST_SHA256
            if step in adapter.GITOPS_STEPS:
                args = ["--core", "--kube-context", self.context,
                        "app", "sync", "fresh-account-release"]
                for resource in adapter.ARGO_SYNC_RESOURCES[step]:
                    args.extend(["--resource", resource])
                args.append("--prune=false")
                run = {"tool": "argocd", "args": args, "contentSha256": None}
            elif step == "cluster":
                run = {"tool": "kind", "args": ["create", "cluster", "--name", self.cluster,
                       "--kubeconfig", self.kubeconfig, "--image",
                       "kindest/node@sha256:" + "a" * 64], "contentSha256": None}
            elif step == "image-import":
                archive = self.temporary / "images.oci.tar"
                archive.write_bytes(b"fixture-oci-archive")
                run = {"tool": "kind", "args": ["load", "image-archive", str(archive.resolve()),
                       "--name", self.cluster],
                       "contentSha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
            elif step == "empty-node-cache":
                run = copy.deepcopy(verify[0])
            elif step == "controllers":
                chart = self.temporary / "chart"
                chart.mkdir(exist_ok=True)
                run = {"tool": "helm", "args": ["--kubeconfig", self.kubeconfig,
                       "--kube-context", self.context, "upgrade", "--install", "argo-rollouts",
                       str(chart.resolve()), "--namespace", "fresh-control", "--version", "2.43.7",
                       "--set", "installCRDs=false",
                       "--set-string", "controller.image.tag=" + adapter.ROLLOUTS_CONTROLLER_TAG,
                       "--atomic", "--wait", "--wait-for-jobs", "--create-namespace"],
                       "contentSha256": adapter._content_sha256(chart)}
            elif step == "acceptance":
                run = {"tool": "rollouts", "args": ["--kubeconfig", self.kubeconfig,
                       "--context", self.context, "restart", "rollout", "account-api",
                       "--namespace", "fresh-account"], "contentSha256": None}
            else:
                run = {"tool": "kubectl", "args": ["--kubeconfig", self.kubeconfig,
                       "--context", self.context, "apply", "--filename", str(manifest.resolve())],
                       "contentSha256": content}
            reconcile = copy.deepcopy(verify)
            if step == "crds":
                for command in reconcile:
                    command["args"].insert(-2, "--ignore-not-found=true")
            return {"phase": adapter.STEP_PHASES[step], "verify": verify,
                    "reconcile": reconcile, "run": [run],
                    "resourceIdentities": identities, "observations": observations,
                    "hostArtifacts": []}
        preflight = {"phase": "pre-cluster",
                     "verify": [{"tool": "kind", "args": ["get", "clusters"],
                                 "contentSha256": None}],
                     "reconcile": [{"tool": "kind", "args": ["get", "clusters"],
                                    "contentSha256": None}],
                     "run": [{"tool": "kind", "args": ["get", "clusters"],
                              "contentSha256": None}],
                     "resourceIdentities": ["HostState/None/target-cluster-absent"],
                     "observations": [], "hostArtifacts": []}
        source = self.temporary / "account-runtime.oci.tar"
        source.write_bytes(b"immutable-account-runtime-archive")
        image_source = {"phase": "pre-cluster", "verify": [], "reconcile": [], "run": [],
                        "resourceIdentities": ["HostArtifact/None/account-runtime-archive"],
                        "observations": [], "hostArtifacts": [{
                            "id": "account-runtime-archive", "path": str(source.resolve()),
                            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                            "sizeBytes": source.stat().st_size,
                            "sourceIdentity": "backend@" + "9" * 40,
                        }]}
        steps = {step: actions(step) for step in adapter.STEPS if step not in {"preflight", "image-source"}}
        steps = {"preflight": preflight, "image-source": image_source, **steps}
        return {
            "schemaVersion": "platform.lab/approved-live-step-plan/v3",
            "target": {"clusterName": self.cluster, "context": self.context,
                       "kubeconfig": self.kubeconfig},
            "tools": tools,
            "identity": {"clusterUID": {"tool": "kubectl", "args": [
                "--kubeconfig", self.kubeconfig, "--context", self.context,
                "get", "namespace", "kube-system", "--output",
                "jsonpath={.metadata.uid}"], "contentSha256": None}},
            "steps": steps,
        }

    def write(self, value):
        path = self.temporary / "live-step-plan.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_validation_is_read_only_and_binds_target_versions_and_tool_bytes(self):
        transport = FakeTransport()
        value = self.plan()
        schema = json.loads((HERE / "live-step-plan.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(list(Draft7Validator(schema).iter_errors(value)), [])
        live = adapter.ApprovedLiveAdapter(
            self.write(value), transport,
            {"clusterName": self.cluster, "context": self.context}, self.versions)
        self.assertEqual(live.plan, value)
        self.assertEqual(live.plan_sha256,
                         hashlib.sha256(live.plan_path.read_bytes()).hexdigest())
        self.assertIs(live.supports_credential_runtime_proof, False)
        self.assertEqual(transport.calls, [])

    def test_full_first_and_rerun_lifecycle_has_semantic_evidence_for_every_step(self):
        transport = FakeTransport()
        live = adapter.ApprovedLiveAdapter(
            self.write(self.plan()), transport,
            {"clusterName": self.cluster, "context": self.context}, self.versions)
        checkpoints = {}
        for step in adapter.STEPS:
            evidence = live.run(step)
            self.assertTrue(evidence["succeeded"], step)
            self.assertRegex(evidence["resourceDigest"], r"^[0-9a-f]{64}$")
            checkpoints[step] = {"status": "succeeded", "evidence": evidence,
                                 "resourceDigest": evidence["resourceDigest"]}
        for step in adapter.STEPS:
            verified = live.verify(step, checkpoints[step])
            self.assertTrue(verified["verified"], step)
            self.assertEqual(verified["resourceDigest"],
                             checkpoints[step]["resourceDigest"], step)
        self.assertEqual(set(checkpoints), set(adapter.STEPS))

    def test_bound_mutation_bytes_are_rehashed_immediately_before_transport(self):
        value = self.plan()
        transport = FakeTransport()
        live = adapter.ApprovedLiveAdapter(
            self.write(value), transport,
            {"clusterName": self.cluster, "context": self.context}, self.versions)
        manifest = Path(value["steps"]["security-boundary"]["run"][0]["args"][-1])
        manifest.write_text("{}", encoding="utf-8")
        calls = len(transport.calls)
        with self.assertRaisesRegex(adapter.AdapterRejected, "command-content-changed"):
            live.run("security-boundary")
        self.assertEqual(len(transport.calls), calls)

    def test_postcluster_empty_cache_and_import_digest_set_are_semantic(self):
        transport = FakeTransport()
        live = adapter.ApprovedLiveAdapter(
            self.write(self.plan()), transport,
            {"clusterName": self.cluster, "context": self.context}, self.versions,
            {"sha256:" + "d" * 64})
        live.run("empty-node-cache")
        with self.assertRaisesRegex(adapter.AdapterRejected,
                                    "observation-node-images-missing"):
            live.verify("image-import", {})
        transport.images_loaded = True
        with self.assertRaisesRegex(adapter.AdapterRejected,
                                    "observation-node-cache-not-empty"):
            live.run("empty-node-cache")

        wrong = self.plan()
        wrong["steps"]["image-import"]["observations"][0]["requiredImageDigests"] = [
            "sha256:" + "e" * 64]
        with self.assertRaisesRegex(adapter.AdapterRejected, "node-image-digest-set"):
            adapter.ApprovedLiveAdapter(
                self.write(wrong), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions,
                {"sha256:" + "d" * 64})

        kind_node_as_workload = self.plan()
        kind_node_as_workload["steps"]["image-import"]["observations"][0][
            "requiredImageDigests"].append("sha256:" + "a" * 64)
        with self.assertRaisesRegex(adapter.AdapterRejected, "node-image-digest-set"):
            adapter.ApprovedLiveAdapter(
                self.write(kind_node_as_workload), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions,
                {"sha256:" + "d" * 64})

    def test_kind_node_and_host_archives_are_bound_to_the_g2_supply_contract(self):
        value = self.plan()
        artifact = value["steps"]["image-source"]["hostArtifacts"][0]
        supply = {
            "kindNodeRef": value["steps"]["cluster"]["run"][0]["args"][-1],
            "hostArtifacts": {artifact["id"]: {
                "sha256": artifact["sha256"],
                "sourceIdentity": artifact["sourceIdentity"],
            }},
            "helmCharts": {"argo-rollouts": {
                "version": "2.43.7",
                "sha256": value["steps"]["controllers"]["run"][0]["contentSha256"],
                "crdListSha256": adapter.ROLLOUTS_CRD_LIST_SHA256,
            }},
        }
        adapter.ApprovedLiveAdapter(
            self.write(value), FakeTransport(),
            {"clusterName": self.cluster, "context": self.context}, self.versions,
            {"sha256:" + "d" * 64}, supply)

        wrong_chart = copy.deepcopy(supply)
        wrong_chart["helmCharts"]["argo-rollouts"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(adapter.AdapterRejected, "supply-rollouts-chart"):
            adapter.ApprovedLiveAdapter(
                self.write(value), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions,
                {"sha256:" + "d" * 64}, wrong_chart)

        wrong_chart_version = copy.deepcopy(supply)
        wrong_chart_version["helmCharts"]["argo-rollouts"]["version"] = "2.43.6"
        with self.assertRaisesRegex(adapter.AdapterRejected, "supply-rollouts-chart"):
            adapter.ApprovedLiveAdapter(
                self.write(value), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions,
                {"sha256:" + "d" * 64}, wrong_chart_version)

        wrong_crds = copy.deepcopy(supply)
        wrong_crds["helmCharts"]["argo-rollouts"]["crdListSha256"] = "f" * 64
        with self.assertRaisesRegex(adapter.AdapterRejected, "supply-rollouts-chart"):
            adapter.ApprovedLiveAdapter(
                self.write(value), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions,
                {"sha256:" + "d" * 64}, wrong_crds)

        wrong_node = copy.deepcopy(value)
        wrong_node["steps"]["cluster"]["run"][0]["args"][-1] = (
            "kindest/node@sha256:" + "f" * 64)
        with self.assertRaisesRegex(adapter.AdapterRejected, "kind-create-command"):
            adapter.ApprovedLiveAdapter(
                self.write(wrong_node), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions,
                {"sha256:" + "d" * 64}, supply)

        profile_spec = {"images": [
            {"name": "kind-node", "ref": supply["kindNodeRef"]},
            {"name": "argo-rollouts", "ref": adapter.ROLLOUTS_CONTROLLER_IMAGE},
        ]}
        adapter.supply_plan_contract({"spec": {"images": []}}, profile_spec)
        profile_spec["images"][1]["ref"] = \
            "quay.io/argoproj/argo-rollouts:v1.10.1"
        with self.assertRaisesRegex(adapter.AdapterRejected,
                                    "supply-rollouts-controller-image"):
            adapter.supply_plan_contract({"spec": {"images": []}}, profile_spec)

        wrong_source = copy.deepcopy(value)
        wrong_source["steps"]["image-source"]["hostArtifacts"][0]["sourceIdentity"] = \
            "image-supply:unapproved"
        with self.assertRaisesRegex(adapter.AdapterRejected,
                                    "host-artifact-supply-identity"):
            adapter.ApprovedLiveAdapter(
                self.write(wrong_source), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions,
                {"sha256:" + "d" * 64}, supply)

        missing = copy.deepcopy(value)
        missing["steps"]["image-source"]["hostArtifacts"] = []
        missing["steps"]["image-source"]["resourceIdentities"] = [
            "HostArtifact/None/missing"]
        with self.assertRaises(adapter.AdapterRejected):
            adapter.ApprovedLiveAdapter(
                self.write(missing), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions,
                {"sha256:" + "d" * 64}, supply)

    def test_argocd_noop_stale_or_wrong_revision_never_counts_as_gitops_success(self):
        mutations = (
            lambda value: value["status"]["operationState"].update(phase="Running")
            if value["kind"] == "Application" else None,
            lambda value: value["status"]["operationState"]["syncResult"].update(
                revision="8" * 40) if value["kind"] == "Application" else None,
            lambda value: value["status"]["sync"].update(status="OutOfSync")
            if value["kind"] == "Application" else None,
            lambda value: value["status"]["health"].update(status="Progressing")
            if value["kind"] == "Application" else None,
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                live = adapter.ApprovedLiveAdapter(
                    self.write(self.plan()), FakeTransport(observation_mutation=mutation),
                    {"clusterName": self.cluster, "context": self.context}, self.versions)
                with self.assertRaises(adapter.AdapterRejected):
                    live.verify("application", {})

    def test_rejects_target_version_tool_tamper_secret_and_mutating_probe(self):
        mutations = (
            lambda value: value["target"].update(context="kind-learning-fresh-other"),
            lambda value: value["tools"]["kubectl"].update(version="v0.0.0"),
            lambda value: Path(value["tools"]["kubectl"]["path"]).write_text("tampered"),
            lambda value: value["steps"]["preflight"]["run"][0]["args"].append("--token=value"),
            lambda value: value["steps"]["preflight"]["verify"][0]["args"].append("delete"),
            lambda value: value["steps"]["preflight"]["verify"][0]["args"].extend(
                ["--context", self.context]),
            lambda value: value["steps"]["preflight"]["verify"][0]["args"].append(
                "--kubeconfig=" + self.kubeconfig),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                value = copy.deepcopy(self.plan())
                mutate(value)
                transport = FakeTransport()
                with self.assertRaises(adapter.AdapterRejected):
                    adapter.ApprovedLiveAdapter(
                        self.write(value), transport,
                        {"clusterName": self.cluster, "context": self.context}, self.versions)
                self.assertEqual(transport.calls, [])

    def test_explicit_adapter_identity_verify_reconcile_and_run(self):
        transport = FakeTransport()
        live = adapter.ApprovedLiveAdapter(
            self.write(self.plan()), transport,
            {"clusterName": self.cluster, "context": self.context}, self.versions)
        identity = live.identity({"clusterName": self.cluster, "context": self.context})
        self.assertEqual(identity, {"context": self.context,
                                    "clusterUID": "fixture-cluster-uid"})
        executed = live.run("preflight")
        checkpoint = {"evidence": executed}
        verified = live.verify("preflight", checkpoint)
        reconciled = live.reconcile("preflight", checkpoint)
        self.assertTrue(verified["verified"])
        self.assertEqual(reconciled["state"], "succeeded")
        self.assertTrue(executed["succeeded"])
        self.assertEqual(executed["resourceDigest"], verified["resourceDigest"])
        self.assertEqual(verified["resourceObservations"][0]["identity"],
                         "HostState/None/target-cluster-absent")
        self.assertIn("clusters", [arg for call in transport.calls for arg in call])
        self.assertTrue(all(env["KUBECONFIG"] == self.kubeconfig
                            for env in transport.environments))
        self.assertTrue(all("ARGOCD_SERVER" not in env for env in transport.environments))

    def test_identity_can_prove_the_precluster_context_is_absent(self):
        transport = FakeTransport(identity_missing=True)
        live = adapter.ApprovedLiveAdapter(
            self.write(self.plan()), transport,
            {"clusterName": self.cluster, "context": self.context}, self.versions)
        self.assertEqual(live.identity({"clusterName": self.cluster, "context": self.context}),
                         {"context": self.context, "clusterUID": None})

    def test_noop_incomplete_wrong_target_stale_and_unhealthy_observations_fail_closed(self):
        cases = (
            FakeTransport(observation_stdout="observed-resource\n"),
            FakeTransport(observation_stdout="{}"),
            FakeTransport(observation_mutation=lambda value: value["metadata"].update(namespace="other")),
            FakeTransport(observation_mutation=lambda value: value["status"].update(observedGeneration=1)),
            FakeTransport(observation_mutation=lambda value: value["status"].update(readyReplicas=0)),
            FakeTransport(observation_mutation=lambda value: value["spec"].update(replicas=2)),
            FakeTransport(observation_mutation=lambda value: value.update(apiVersion="apps/v2")),
        )
        for transport in cases:
            with self.subTest(transport=transport):
                live = adapter.ApprovedLiveAdapter(
                    self.write(self.plan()), transport,
                    {"clusterName": self.cluster, "context": self.context}, self.versions)
                with self.assertRaises(adapter.AdapterRejected):
                    live.verify("preflight", {})

    def test_database_and_application_use_exact_manifest_identity_and_readiness(self):
        live = adapter.ApprovedLiveAdapter(
            self.write(self.plan()), FakeTransport(),
            {"clusterName": self.cluster, "context": self.context}, self.versions)
        database = live.verify("database", {})
        application = live.verify("application", {})
        self.assertEqual(
            {item["identity"] for item in database["resourceObservations"]},
            {"Application/fresh-control/fresh-account-release",
             "PersistentVolumeClaim/fresh-account/account-data",
             "Service/fresh-account/account-postgresql",
             "StatefulSet/fresh-account/account-postgresql"})
        self.assertEqual(
            {item["identity"] for item in application["resourceObservations"]},
            {"Application/fresh-control/fresh-account-release",
             "Rollout/fresh-account/account-api", "Service/fresh-account/account-api",
             "DestinationRule/fresh-account/account-api",
             "VirtualService/fresh-account/account-api"})

    def test_missing_wrong_or_fabricated_database_and_application_contract_is_rejected(self):
        mutations = (
            lambda value: value["steps"]["database"]["observations"].pop(),
            lambda value: value["steps"]["database"]["observations"][1].update(
                identity="Deployment/fresh-control/database", apiVersion="apps/v1",
                healthPolicy="workload-ready", requiredConditions={"Available": "True"}),
            lambda value: value["steps"]["application"]["observations"][0].update(
                apiVersion="apps/v1"),
            lambda value: value["steps"]["application"]["observations"][0].update(
                identity="Rollout/fresh-control/account-api"),
            lambda value: value["steps"]["application"]["observations"][0].update(
                healthPolicy="conditions", requiredConditions={"Available": "True"}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                value = self.plan()
                mutate(value)
                # Keep the old parallel arrays aligned where possible; exact set validation
                # must still reject the wrong or incomplete contract.
                value["steps"]["database"]["resourceIdentities"] = [
                    item["identity"] for item in value["steps"]["database"]["observations"]]
                value["steps"]["application"]["resourceIdentities"] = [
                    item["identity"] for item in value["steps"]["application"]["observations"]]
                with self.assertRaisesRegex(
                        adapter.AdapterRejected,
                        "(step-(resource-contract|semantic-health)|observation-)"):
                    adapter.ApprovedLiveAdapter(
                        self.write(value), FakeTransport(),
                        {"clusterName": self.cluster, "context": self.context}, self.versions)

    def test_real_database_and_rollout_stale_or_fake_readiness_is_rejected(self):
        cases = (
            ("database", lambda value: value["status"].update(readyReplicas=0)),
            ("database", lambda value: value["status"].update(updateRevision="other")),
            ("database", lambda value: value["status"].update(phase="Pending")),
            ("application", lambda value: value["status"].update(readyReplicas=0)),
            ("application", lambda value: value["status"].update(stableRS="other")),
            ("application", lambda value: value["status"].update(
                conditions=[{"type": "Available", "status": "False"}])),
        )
        for step, mutation in cases:
            with self.subTest(step=step, mutation=mutation):
                live = adapter.ApprovedLiveAdapter(
                    self.write(self.plan()), FakeTransport(observation_mutation=mutation),
                    {"clusterName": self.cluster, "context": self.context}, self.versions)
                with self.assertRaises(adapter.AdapterRejected):
                    live.verify(step, {})

    def test_positive_command_grammar_rejects_server_scale_helm_override_and_unknown_flags(self):
        attacks = []
        kubectl_server = self.plan()
        kubectl_server["steps"]["preflight"]["verify"][0]["args"].extend(
            ["--server", "https://other.example"])
        attacks.append(kubectl_server)

        kubectl_scale = self.plan()
        kubectl_scale["steps"]["preflight"]["verify"][0]["args"] = [
            "--kubeconfig", self.kubeconfig, "--context", self.context,
            "scale", "deployment", "preflight", "--namespace", "fresh-control",
            "--replicas=0"]
        attacks.append(kubectl_scale)

        helm_override = self.plan()
        helm_override["steps"]["controllers"]["run"][0]["args"][4:4] = [
            "--kube-apiserver", "https://other.example"]
        attacks.append(helm_override)

        kubectl_unknown = self.plan()
        kubectl_unknown["steps"]["preflight"]["verify"][0]["args"].append("--raw=/api")
        attacks.append(kubectl_unknown)

        for index, value in enumerate(attacks):
            with self.subTest(index=index):
                with self.assertRaises(adapter.AdapterRejected):
                    adapter.ApprovedLiveAdapter(
                        self.write(value), FakeTransport(),
                        {"clusterName": self.cluster, "context": self.context}, self.versions)

    def test_rollouts_controller_is_single_pinned_and_never_owns_crds(self):
        value = self.plan()
        adapter.ApprovedLiveAdapter(
            self.write(value), FakeTransport(),
            {"clusterName": self.cluster, "context": self.context}, self.versions)

        wrong_release = copy.deepcopy(value)
        wrong_release["steps"]["controllers"]["run"][0]["args"][6] = "other"
        with self.assertRaises(adapter.AdapterRejected):
            adapter.ApprovedLiveAdapter(
                self.write(wrong_release), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions)

        for mutation in (
            lambda args: args.__setitem__(11, "2.43.6"),
            lambda args: args.__setitem__(13, "installCRDs=true"),
            lambda args: args.__setitem__(15, "controller.image.tag=v1.10.1"),
            lambda args: args.__delitem__(slice(12, 14)),
            lambda args: args.__delitem__(slice(14, 16)),
            lambda args: args.extend(["--set", "installCRDs=false"]),
        ):
            changed = copy.deepcopy(value)
            mutation(changed["steps"]["controllers"]["run"][0]["args"])
            with self.assertRaises(adapter.AdapterRejected):
                adapter.ApprovedLiveAdapter(
                    self.write(changed), FakeTransport(),
                    {"clusterName": self.cluster, "context": self.context}, self.versions)

        duplicate = copy.deepcopy(value)
        duplicate["steps"]["controllers"]["run"].append(
            copy.deepcopy(duplicate["steps"]["controllers"]["run"][0]))
        with self.assertRaisesRegex(adapter.AdapterRejected,
                                    "rollouts-controller-install-count"):
            adapter.ApprovedLiveAdapter(
                self.write(duplicate), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions)

        wrong_observation = copy.deepcopy(value)
        wrong_observation["steps"]["controllers"]["observations"][0]["identity"] = \
            "Deployment/fresh-control/not-argo-rollouts"
        wrong_observation["steps"]["controllers"]["resourceIdentities"][0] = \
            "Deployment/fresh-control/not-argo-rollouts"
        with self.assertRaisesRegex(adapter.AdapterRejected,
                                    "rollouts-controller-observation"):
            adapter.ApprovedLiveAdapter(
                self.write(wrong_observation), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions)

        fake_crd_release = copy.deepcopy(value)
        command = copy.deepcopy(value["steps"]["controllers"]["run"][0])
        command["args"][6] = "argo-rollouts-crds"
        fake_crd_release["steps"]["crds"]["run"] = [command]
        with self.assertRaisesRegex(adapter.AdapterRejected, "crds-single-apply"):
            adapter.ApprovedLiveAdapter(
                self.write(fake_crd_release), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions)

    def test_crd_set_is_exact_and_manifest_bound(self):
        for mutation in (
            lambda items: items.pop(),
            lambda items: items.append(copy.deepcopy(items[0])),
            lambda items: items[0]["metadata"].update(name="wrong.argoproj.io"),
        ):
            changed = self.plan()
            manifest_path = Path(changed["steps"]["crds"]["run"][0]["args"][-1])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            mutation(manifest["items"])
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            changed["steps"]["crds"]["run"][0]["contentSha256"] = \
                hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            with self.assertRaises(adapter.AdapterRejected):
                adapter.ApprovedLiveAdapter(
                    self.write(changed), FakeTransport(),
                    {"clusterName": self.cluster, "context": self.context}, self.versions)

        changed = self.plan()
        manifest_path = Path(changed["steps"]["crds"]["run"][0]["args"][-1])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["items"][0]["spec"]["group"] = "attacker.example"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        observation = changed["steps"]["crds"]["observations"][0]
        observation["desiredSha256"] = adapter._mapping_digest(
            {"/spec": manifest["items"][0]["spec"]})
        changed["steps"]["crds"]["run"][0]["contentSha256"] = \
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        with mock.patch.object(adapter, "_sha256", side_effect=self.real_sha256):
            with self.assertRaisesRegex(adapter.AdapterRejected,
                                        "crds-official-render-hash"):
                adapter.ApprovedLiveAdapter(
                    self.write(changed), FakeTransport(),
                    {"clusterName": self.cluster, "context": self.context}, self.versions)

    def test_all_crds_require_immutable_established_true_contract(self):
        for status in ("False", "Unknown"):
            changed = self.plan()
            for observation in changed["steps"]["crds"]["observations"]:
                observation["requiredConditions"] = {"Established": status}
            with self.assertRaisesRegex(adapter.AdapterRejected,
                                        "crd-established-contract"):
                adapter.ApprovedLiveAdapter(
                    self.write(changed), FakeTransport(),
                    {"clusterName": self.cluster, "context": self.context}, self.versions)

    def test_crd_reconcile_treats_only_explicit_not_found_as_incomplete(self):
        missing = "experiments.argoproj.io"
        transport = FakeTransport(missing_crds={missing})
        live = adapter.ApprovedLiveAdapter(
            self.write(self.plan()), transport,
            {"clusterName": self.cluster, "context": self.context}, self.versions)
        self.assertEqual(live.reconcile("crds", {})["state"], "incomplete")
        self.assertTrue(any("--ignore-not-found=true" in call for call in transport.calls))

        transport.missing_crds.clear()
        reconciled = live.reconcile("crds", {})
        self.assertEqual(reconciled["state"], "succeeded")
        self.assertEqual(len(reconciled["evidence"]["resourceObservations"]), 5)

        for returncode, error in (
                (1, "Error from server (Forbidden): forbidden"),
                (1, "Unable to connect to the server: connection refused"),
                (3, "arbitrary non-NotFound failure")):
            with self.subTest(returncode=returncode, error=error):
                failing = adapter.ApprovedLiveAdapter(
                    self.write(self.plan()), FakeTransport(
                        crd_error=error, crd_returncode=returncode),
                    {"clusterName": self.cluster, "context": self.context}, self.versions)
                with self.assertRaisesRegex(
                        adapter.AdapterRejected, f"command-exit:{returncode}"):
                    failing.reconcile("crds", {})

    def test_known_manifest_gvk_and_desired_state_are_exact(self):
        value = self.plan()

        wrong_gvk = self.plan()
        manifest_path = Path(wrong_gvk["steps"]["security-boundary"]["run"][0]["args"][-1])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["items"][0]["apiVersion"] = "apps/v2"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        wrong_gvk["steps"]["security-boundary"]["run"][0]["contentSha256"] = \
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(adapter.AdapterRejected, "kubectl-manifest-targets"):
            adapter.ApprovedLiveAdapter(
                self.write(wrong_gvk), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions)

        wrong_desired = self.plan()
        manifest_path = Path(wrong_desired["steps"]["security-boundary"]["run"][0]["args"][-1])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["items"][0]["spec"]["replicas"] = 0
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        wrong_desired["steps"]["security-boundary"]["run"][0]["contentSha256"] = \
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(adapter.AdapterRejected, "kubectl-manifest-desired-digest"):
            adapter.ApprovedLiveAdapter(
                self.write(wrong_desired), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions)

    def test_argocd_core_is_context_bound_and_ambient_target_override_is_rejected(self):
        value = self.plan()
        adapter.ApprovedLiveAdapter(
            self.write(value), FakeTransport(),
            {"clusterName": self.cluster, "context": self.context}, self.versions)

        duplicate = copy.deepcopy(value)
        duplicate["steps"]["database"]["verify"][0]["args"].extend(
            ["--kube-context", self.context])
        with self.assertRaises(adapter.AdapterRejected):
            adapter.ApprovedLiveAdapter(
                self.write(duplicate), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions)

        with mock.patch.dict(os.environ, {"KUBECONFIG": str(self.temporary / "other.kubeconfig")}, clear=False):
            with self.assertRaisesRegex(adapter.AdapterRejected, "environment-target-override"):
                adapter.ApprovedLiveAdapter(
                    self.write(self.plan()), FakeTransport(),
                    {"clusterName": self.cluster, "context": self.context}, self.versions)


if __name__ == "__main__":
    unittest.main(verbosity=2)
