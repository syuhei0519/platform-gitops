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

    def run(self, argv, env):
        self.calls.append(argv)
        self.environments.append(env)
        if "jsonpath={.metadata.uid}" in argv:
            if self.identity_missing:
                return SimpleNamespace(returncode=1, stdout="", stderr="context absent")
            stdout = "fixture-cluster-uid\n"
        elif "apply" in argv:
            stdout = "applied\n"
        else:
            if self.observation_stdout is not None:
                stdout = self.observation_stdout
            else:
                get_index = argv.index("get")
                resource_kind, name = argv[get_index + 1:get_index + 3]
                namespace = argv[argv.index("--namespace") + 1]
                api_version, kind = adapter.KUBECTL_RESOURCES[resource_kind]
                spec = ApprovedLiveAdapterTests.resource_spec(kind, name)
                status = ApprovedLiveAdapterTests.resource_status(kind)
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
        if kind == "Job":
            return {"template": {"metadata": {"labels": {"platform.lab/step": name}}}}
        if kind == "PersistentVolumeClaim":
            return {"accessModes": ["ReadWriteOnce"], "volumeName": "pvc-account-data"}
        if kind in {"Deployment", "StatefulSet", "Rollout"}:
            return {"replicas": 1, "template": {"metadata":
                    {"labels": {"platform.lab/step": name}}}}
        return {"selector": {"app": name}}

    @staticmethod
    def resource_status(kind):
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
        return {"tool": "kubectl", "args": ["--kubeconfig", self.kubeconfig,
                "--context", self.context, "get", self.RESOURCE_TOKEN[kind], name,
                "--namespace", namespace, "--output", "json"]}

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
            if step == "database":
                return [
                    ("v1", "PersistentVolumeClaim", "fresh-account", "account-data",
                     "none", "pvc-bound", {}),
                    ("apps/v1", "StatefulSet", "fresh-account", "account-postgresql",
                     "observed-current", "statefulset-ready", {}),
                ]
            if step == "application":
                return [
                    ("argoproj.io/v1alpha1", "Rollout", "fresh-account", "account-api",
                     "observed-current", "rollout-ready", {"Available": "True"}),
                    ("v1", "Service", "fresh-account", "account-api",
                     "none", "none", {}),
                    ("networking.istio.io/v1beta1", "DestinationRule", "fresh-account",
                     "account-api", "none", "none", {}),
                    ("networking.istio.io/v1beta1", "VirtualService", "fresh-account",
                     "account-api", "none", "none", {}),
                ]
            kind = "Job" if step in {"role-bootstrap", "migration"} else "Deployment"
            api_version = "batch/v1" if kind == "Job" else "apps/v1"
            return [(api_version, kind, "fresh-control", step,
                     "none" if kind == "Job" else "observed-current",
                     "job-complete" if kind == "Job" else "workload-ready",
                     {"Complete": "True"} if kind == "Job" else {"Available": "True"})]

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
                })
                manifests.append({"apiVersion": api_version, "kind": kind,
                                  "metadata": {"namespace": namespace, "name": name},
                                  "spec": spec})
            manifest = self.temporary / f"{step}.json"
            manifest.write_text(json.dumps({"apiVersion": "v1", "kind": "List",
                                             "items": manifests}), encoding="utf-8")
            run = {"tool": "kubectl", "args": ["--kubeconfig", self.kubeconfig,
                   "--context", self.context, "apply", "--filename", str(manifest.resolve())]}
            return {"verify": verify, "reconcile": copy.deepcopy(verify), "run": [run],
                    "resourceIdentities": identities, "observations": observations}
        return {
            "schemaVersion": "platform.lab/approved-live-step-plan/v2",
            "target": {"clusterName": self.cluster, "context": self.context,
                       "kubeconfig": self.kubeconfig},
            "tools": tools,
            "identity": {"clusterUID": {"tool": "kubectl", "args": [
                "--kubeconfig", self.kubeconfig, "--context", self.context,
                "get", "namespace", "kube-system", "--output",
                "jsonpath={.metadata.uid}"]}},
            "steps": {step: actions(step) for step in adapter.STEPS},
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
        verified = live.verify("preflight", {})
        reconciled = live.reconcile("preflight", {})
        executed = live.run("preflight")
        self.assertTrue(verified["verified"])
        self.assertEqual(reconciled["state"], "succeeded")
        self.assertTrue(executed["succeeded"])
        self.assertEqual(executed["resourceDigest"], verified["resourceDigest"])
        self.assertEqual(verified["resourceObservations"][0]["identity"],
                         "Deployment/fresh-control/preflight")
        self.assertIn("apply", [arg for call in transport.calls for arg in call])
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
            {"PersistentVolumeClaim/fresh-account/account-data",
             "StatefulSet/fresh-account/account-postgresql"})
        self.assertEqual(
            {item["identity"] for item in application["resourceObservations"]},
            {"Rollout/fresh-account/account-api", "Service/fresh-account/account-api",
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
                        adapter.AdapterRejected, "step-(resource-contract|semantic-health)"):
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
            "--wait-for-jobs", "--create-namespace"]}
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
            "--atomic", "--wait", "--wait-for-jobs", "--create-namespace"]}
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
        manifest_path = Path(wrong_gvk["steps"]["preflight"]["run"][0]["args"][-1])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["items"][0]["apiVersion"] = "apps/v2"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(adapter.AdapterRejected, "kubectl-manifest-targets"):
            adapter.ApprovedLiveAdapter(
                self.write(wrong_gvk), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions)

        wrong_desired = self.plan()
        manifest_path = Path(wrong_desired["steps"]["preflight"]["run"][0]["args"][-1])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["items"][0]["spec"]["replicas"] = 0
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(adapter.AdapterRejected, "kubectl-manifest-desired-digest"):
            adapter.ApprovedLiveAdapter(
                self.write(wrong_desired), FakeTransport(),
                {"clusterName": self.cluster, "context": self.context}, self.versions)

    def test_argocd_core_is_context_bound_and_ambient_target_override_is_rejected(self):
        value = self.plan()
        app_spec = {"project": "fresh-platform"}
        app_digest = hashlib.sha256(json.dumps({"/spec": app_spec}, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()
        value["steps"]["preflight"]["resourceIdentities"] = [
            "Application/fresh-control/account-lightweight-root"]
        value["steps"]["preflight"]["observations"] = [{
            "apiVersion": "argoproj.io/v1alpha1",
            "identity": "Application/fresh-control/account-lightweight-root",
            "desiredPaths": ["/spec"], "desiredSha256": app_digest,
            "generationPolicy": "none", "healthPolicy": "none",
            "requiredConditions": {},
        }]
        app_manifest = self.temporary / "preflight-application.json"
        app_manifest.write_text(json.dumps({
            "apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
            "metadata": {"namespace": "fresh-control", "name": "account-lightweight-root"},
            "spec": app_spec,
        }), encoding="utf-8")
        value["steps"]["preflight"]["run"] = [{
            "tool": "kubectl", "args": ["--kubeconfig", self.kubeconfig,
            "--context", self.context, "apply", "--filename", str(app_manifest.resolve())]}]
        value["steps"]["preflight"]["verify"][0] = {
            "tool": "argocd", "args": ["--core", "--kube-context", self.context,
                                         "app", "get", "account-lightweight-root",
                                         "--output", "json"]}
        value["steps"]["preflight"]["reconcile"][0] = copy.deepcopy(
            value["steps"]["preflight"]["verify"][0])
        adapter.ApprovedLiveAdapter(
            self.write(value), FakeTransport(),
            {"clusterName": self.cluster, "context": self.context}, self.versions)

        duplicate = copy.deepcopy(value)
        duplicate["steps"]["preflight"]["verify"][0]["args"].extend(
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
