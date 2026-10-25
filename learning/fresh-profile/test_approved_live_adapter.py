#!/usr/bin/env python3
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest

from jsonschema import Draft7Validator


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("approved_live_adapter", HERE / "approved_live_adapter.py")
adapter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(adapter)


class FakeTransport:
    def __init__(self, identity_missing=False):
        self.calls = []
        self.identity_missing = identity_missing

    def run(self, argv):
        self.calls.append(argv)
        if "identity-context" in argv:
            stdout = "kind-learning-fresh-plat217-20261025\n"
        elif "identity-uid" in argv:
            if self.identity_missing:
                return SimpleNamespace(returncode=1, stdout="", stderr="context absent")
            stdout = "fixture-cluster-uid\n"
        else:
            stdout = "observed-resource\n"
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

    def command(self, verb, name="fixture"):
        return {"tool": "kubectl", "args": ["--kubeconfig", self.kubeconfig,
                "--context", self.context, verb, "configmap", name,
                "--namespace", "fresh-control"]}

    def plan(self):
        tools = {}
        for alias, version in self.versions.items():
            path = self.temporary / (alias + ".fixture")
            path.write_text(alias + ":" + version + "\n", encoding="utf-8")
            tools[alias] = {"path": str(path.resolve()),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "version": version}
        return {
            "schemaVersion": "platform.lab/approved-live-step-plan/v1",
            "target": {"clusterName": self.cluster, "context": self.context,
                       "kubeconfig": self.kubeconfig},
            "tools": tools,
            "identity": {"clusterUID": self.command("get", "identity-uid")},
            "steps": {step: {"verify": [self.command("get", step)],
                              "reconcile": [self.command("get", step)],
                              "run": [self.command("apply", step)],
                              "resourceIdentities": ["ConfigMap/fresh-control/" + step]}
                      for step in adapter.STEPS},
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
        self.assertIn("apply", [arg for call in transport.calls for arg in call])

    def test_identity_can_prove_the_precluster_context_is_absent(self):
        transport = FakeTransport(identity_missing=True)
        live = adapter.ApprovedLiveAdapter(
            self.write(self.plan()), transport,
            {"clusterName": self.cluster, "context": self.context}, self.versions)
        self.assertEqual(live.identity({"clusterName": self.cluster, "context": self.context}),
                         {"context": self.context, "clusterUID": None})


if __name__ == "__main__":
    unittest.main(verbosity=2)
