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
        if "identity-uid" in argv:
            if self.identity_missing:
                return SimpleNamespace(returncode=1, stdout="", stderr="context absent")
            stdout = "fixture-cluster-uid\n"
        else:
            if self.observation_stdout is not None:
                stdout = self.observation_stdout
            else:
                resource_kind = "job" if "job" in argv else "deployment"
                name = argv[argv.index(resource_kind) + 1]
                kind = "Job" if resource_kind == "job" else "Deployment"
                spec = ({"template": {"metadata": {"labels": {"platform.lab/step": name}}}}
                        if kind == "Job" else
                        {"replicas": 1, "template": {"metadata":
                         {"labels": {"platform.lab/step": name}}}})
                status = ({"succeeded": 1, "completionTime": "2026-10-25T13:00:00Z",
                           "conditions": [{"type": "Complete", "status": "True"}]}
                          if kind == "Job" else
                          {"observedGeneration": 2, "readyReplicas": 1,
                           "availableReplicas": 1,
                           "conditions": [{"type": "Available", "status": "True"}]})
                value = {
                    "apiVersion": "batch/v1" if kind == "Job" else "apps/v1", "kind": kind,
                    "metadata": {"name": name, "namespace": "fresh-control",
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

    def command(self, verb, name="fixture", output=False, resource="deployment"):
        suffix = ["--namespace", "fresh-control"]
        if output:
            suffix += ["--output", "json"]
        return {"tool": "kubectl", "args": ["--kubeconfig", self.kubeconfig,
                "--context", self.context, verb, resource, name, *suffix]}

    @staticmethod
    def desired_digest(name, resource="deployment"):
        spec = ({"template": {"metadata": {"labels": {"platform.lab/step": name}}}}
                if resource == "job" else
                {"replicas": 1, "template": {"metadata":
                 {"labels": {"platform.lab/step": name}}}})
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
        def actions(step):
            resource = "job" if step in {"role-bootstrap", "migration"} else "deployment"
            kind = "Job" if resource == "job" else "Deployment"
            health = "job-complete" if resource == "job" else "workload-ready"
            conditions = {"Complete": "True"} if resource == "job" else {"Available": "True"}
            generation = "none" if resource == "job" else "observed-current"
            return {"verify": [self.command("get", step, output=True, resource=resource)],
                    "reconcile": [self.command("get", step, output=True, resource=resource)],
                    "run": [self.command("apply", step, resource=resource)],
                    "resourceIdentities": [f"{kind}/fresh-control/{step}"],
                    "observations": [{
                        "identity": f"{kind}/fresh-control/{step}",
                        "desiredPaths": ["/spec"],
                        "desiredSha256": self.desired_digest(step, resource),
                        "generationPolicy": generation,
                        "healthPolicy": health,
                        "requiredConditions": conditions,
                    }]}
        return {
            "schemaVersion": "platform.lab/approved-live-step-plan/v2",
            "target": {"clusterName": self.cluster, "context": self.context,
                       "kubeconfig": self.kubeconfig},
            "tools": tools,
            "identity": {"clusterUID": self.command("get", "identity-uid")},
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
        )
        for transport in cases:
            with self.subTest(transport=transport):
                live = adapter.ApprovedLiveAdapter(
                    self.write(self.plan()), transport,
                    {"clusterName": self.cluster, "context": self.context}, self.versions)
                with self.assertRaises(adapter.AdapterRejected):
                    live.verify("preflight", {})

    def test_argocd_core_is_context_bound_and_ambient_target_override_is_rejected(self):
        value = self.plan()
        value["steps"]["preflight"]["verify"][0] = {
            "tool": "argocd", "args": ["--core", "--kube-context", self.context,
                                         "app", "get", "preflight", "--output", "json"]}
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
