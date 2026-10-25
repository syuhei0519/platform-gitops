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
                 observation_stdout=None):
        self.calls = []
        self.environments = []
        self.identity_missing = identity_missing
        self.observation_mutation = observation_mutation
        self.observation_stdout = observation_stdout
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
                    namespace = ("None" if kind == "Node"
                                 else argv[argv.index("--namespace") + 1])
                spec = ApprovedLiveAdapterTests.resource_spec(kind, name)
                status = ApprovedLiveAdapterTests.resource_status(
                    kind, images_loaded=self.images_loaded)
                value = {
                    "apiVersion": api_version, "kind": kind,
                    "metadata": {"name": name, "namespace": namespace,
                                 "uid": "uid-" + name, "generation": 2},
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
        self.context = "kind-learning-fresh-plat217-20261025"
        self.cluster = "learning-fresh-plat217-20261025"
        self.kubeconfig = str((self.temporary / "fresh.kubeconfig").resolve())
        self.versions = {
            "kind": "v0.33.0", "kubectl": "v1.36.4", "helm": "v4.2.4",
            "istioctl": "1.31.2", "argocd": "v3.5.2", "rollouts": "v1.10.1",
        }

    RESOURCE_TOKEN = {
        "Deployment": "deployment", "Job": "job", "PersistentVolumeClaim": "pvc",
        "StatefulSet": "statefulset", "Rollout": "rollout", "Service": "service",
        "DestinationRule": "destinationrule", "VirtualService": "virtualservice",
        "Application": "application",
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
                manifests.append({"apiVersion": api_version, "kind": kind,
                                  "metadata": {"namespace": namespace, "name": name},
                                  "spec": spec})
            manifest = self.temporary / f"{step}.json"
            manifest.write_text(json.dumps({"apiVersion": "v1", "kind": "List",
                                             "items": manifests}), encoding="utf-8")
            content = hashlib.sha256(manifest.read_bytes()).hexdigest()
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
            elif step in {"crds", "controllers"}:
                chart = self.temporary / "chart"
                chart.mkdir(exist_ok=True)
                release, namespace = (("argo-rollouts-crds", "fresh-control") if step == "crds"
                                      else ("argo-cd", "fresh-control"))
                run = {"tool": "helm", "args": ["--kubeconfig", self.kubeconfig,
                       "--kube-context", self.context, "upgrade", "--install", release,
                       str(chart.resolve()), "--namespace", namespace, "--version", "1.0.0",
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
            return {"phase": adapter.STEP_PHASES[step], "verify": verify,
                    "reconcile": copy.deepcopy(verify), "run": [run],
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

    def test_kind_node_and_host_archives_are_bound_to_the_g2_supply_contract(self):
        value = self.plan()
        artifact = value["steps"]["image-source"]["hostArtifacts"][0]
        supply = {
            "kindNodeRef": value["steps"]["cluster"]["run"][0]["args"][-1],
            "hostArtifacts": {artifact["id"]: {
                "sha256": artifact["sha256"],
                "sourceIdentity": artifact["sourceIdentity"],
            }},
        }
        adapter.ApprovedLiveAdapter(
            self.write(value), FakeTransport(),
            {"clusterName": self.cluster, "context": self.context}, self.versions,
            {"sha256:" + "d" * 64}, supply)

        wrong_node = copy.deepcopy(value)
        wrong_node["steps"]["cluster"]["run"][0]["args"][-1] = (
            "kindest/node@sha256:" + "f" * 64)
        with self.assertRaisesRegex(adapter.AdapterRejected, "kind-create-command"):
            adapter.ApprovedLiveAdapter(
                self.write(wrong_node), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions,
                {"sha256:" + "d" * 64}, supply)

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
        chart = self.temporary / "chart"
        chart.mkdir(exist_ok=True)
        helm_override["steps"]["controllers"]["run"][0] = {
            "tool": "helm", "args": ["--kubeconfig", self.kubeconfig,
            "--kube-context", self.context, "--kube-apiserver", "https://other.example",
            "upgrade", "--install", "argo-cd", str(chart.resolve()),
            "--namespace", "fresh-control", "--version", "1.0.0", "--atomic", "--wait",
            "--wait-for-jobs", "--create-namespace"],
            "contentSha256": adapter._content_sha256(chart)}
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

    def test_known_helm_shape_is_allowed_but_release_and_manifest_gvk_are_exact(self):
        chart = self.temporary / "chart"
        chart.mkdir(exist_ok=True)
        value = self.plan()
        value["steps"]["controllers"]["run"][0] = {
            "tool": "helm", "args": ["--kubeconfig", self.kubeconfig,
            "--kube-context", self.context, "upgrade", "--install", "argo-cd",
            str(chart.resolve()), "--namespace", "fresh-control", "--version", "1.0.0",
            "--atomic", "--wait", "--wait-for-jobs", "--create-namespace"],
            "contentSha256": adapter._content_sha256(chart)}
        adapter.ApprovedLiveAdapter(
            self.write(value), FakeTransport(),
            {"clusterName": self.cluster, "context": self.context}, self.versions)

        wrong_release = copy.deepcopy(value)
        wrong_release["steps"]["controllers"]["run"][0]["args"][6] = "other"
        with self.assertRaises(adapter.AdapterRejected):
            adapter.ApprovedLiveAdapter(
                self.write(wrong_release), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions)

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
