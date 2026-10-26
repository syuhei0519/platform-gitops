#!/usr/bin/env python3

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SYNTHETIC_UID = "-".join(("1" * 8, "1" * 4, "4" + "1" * 3,
                          "8" + "1" * 3, "1" * 12))
SPEC = importlib.util.spec_from_file_location(
    "capture_adoption", HERE / "capture_adoption.py")
capture = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(capture)


class CaptureAdoptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capture.RUNTIME_ROOT.mkdir(exist_ok=True)

    def temporary_runtime(self):
        return tempfile.TemporaryDirectory(dir=capture.RUNTIME_ROOT)

    def runtime_files(self, directory: Path, *, context="kind-synthetic-fixture"):
        contract = directory / "capture.runtime.local.json"
        inventory = directory / "inventory.runtime.local.json"
        contract.write_text(json.dumps({
            "apiVersion": "platform.lab/v1alpha1",
            "kind": "ExistingKindCaptureRuntime",
            "target": {"context": context, "kubeSystemUid": SYNTHETIC_UID},
            "namespaces": {
                "services": "fixture-services",
                "observability": "fixture-observability",
                "meshFixture": "fixture-mesh",
                "argocdCore": "fixture-control",
            },
            "exclusions": {
                "names": {"fixture-control": ["fixture-config"]},
                "prefixes": {"fixture-services": ["fixture-controller-"]},
            },
        }), encoding="utf-8")
        inventory.write_text(json.dumps({
            "collectedAt": None, "context": context, "resources": [], "errors": [],
        }), encoding="utf-8")
        return contract, inventory

    def test_external_runtime_contract_is_accepted(self):
        with self.temporary_runtime() as directory:
            contract, inventory = self.runtime_files(Path(directory))
            target, namespaces, exclusions, loaded = capture.load_runtime(
                contract, inventory, "kind-synthetic-fixture")
            self.assertEqual(target["kubeSystemUid"], SYNTHETIC_UID)
            self.assertEqual(set(namespaces), capture.GROUPS)
            self.assertEqual(exclusions["names"]["fixture-control"], ["fixture-config"])
            self.assertEqual(loaded["resources"], [])

    def test_checked_in_template_cannot_be_runtime_input(self):
        with self.assertRaisesRegex(capture.CaptureRejected, "tracked-runtime-input"):
            capture.runtime_file(HERE / "inventory.template.json")

    def test_checked_in_output_is_rejected(self):
        with self.assertRaisesRegex(capture.CaptureRejected, "tracked-runtime-output"):
            capture.runtime_output(HERE / "would-be-tracked-output")

    def test_context_mismatch_fails_closed(self):
        with self.temporary_runtime() as directory:
            contract, inventory = self.runtime_files(Path(directory))
            with self.assertRaisesRegex(capture.CaptureRejected, "runtime-contract"):
                capture.load_runtime(contract, inventory, "kind-other-fixture")

    def test_invalid_exclusion_namespace_fails_closed(self):
        with self.temporary_runtime() as directory:
            contract, inventory = self.runtime_files(Path(directory))
            value = json.loads(contract.read_text(encoding="utf-8"))
            value["exclusions"]["names"] = {"unapproved-namespace": ["value"]}
            contract.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(capture.CaptureRejected, "runtime-exclusions"):
                capture.load_runtime(contract, inventory, "kind-synthetic-fixture")

    def test_public_templates_cannot_be_used_as_runtime_evidence(self):
        with self.temporary_runtime() as directory:
            root = Path(directory)
            contract = root / "capture.runtime.local.json"
            inventory = root / "inventory.runtime.local.json"
            contract.write_bytes((HERE / "capture-runtime.template.json").read_bytes())
            inventory.write_bytes((HERE / "inventory.template.json").read_bytes())
            with self.assertRaisesRegex(capture.CaptureRejected,
                                        "runtime-contract|runtime-placeholder"):
                capture.load_runtime(
                    contract, inventory, "<runtime-input:kube-context>")

    def identity_contract(self):
        namespaces = {
            "services": "fixture-services",
            "observability": "fixture-observability",
            "meshFixture": "fixture-mesh",
            "argocdCore": "fixture-control",
        }
        exclusions = {"names": {}, "prefixes": {}}
        key = ("Deployment", "fixture-services", "fixture-api")
        inventory = {"resources": [{
            "kind": key[0], "namespace": key[1], "name": key[2],
            "uid": SYNTHETIC_UID,
        }]}
        return capture.expected_identities(inventory, namespaces, exclusions), key

    def test_missing_expected_identity_fails_closed(self):
        expected, _ = self.identity_contract()
        with self.assertRaisesRegex(capture.CaptureRejected,
                                    "identity-set-mismatch:missing"):
            capture.require_complete_identity_set(expected, {})

    def test_extra_observed_identity_fails_closed(self):
        expected, key = self.identity_contract()
        observed = dict(expected)
        observed[("Service", key[1], "unexpected")] = SYNTHETIC_UID
        with self.assertRaisesRegex(capture.CaptureRejected,
                                    "identity-set-mismatch:.*extra"):
            capture.require_complete_identity_set(expected, observed)

    def test_duplicate_observed_identity_fails_closed(self):
        expected, key = self.identity_contract()
        observed = {}
        capture.record_observed(expected, observed, key, SYNTHETIC_UID,
                                "parent-identity-changed")
        with self.assertRaisesRegex(capture.CaptureRejected,
                                    "duplicate-observed-identity"):
            capture.record_observed(expected, observed, key, SYNTHETIC_UID,
                                    "parent-identity-changed")

    def test_uid_mismatch_fails_closed(self):
        expected, key = self.identity_contract()
        with self.assertRaisesRegex(capture.CaptureRejected,
                                    "parent-identity-changed"):
            capture.record_observed(expected, {}, key, SYNTHETIC_UID[:-1] + "2",
                                    "parent-identity-changed")

    def test_duplicate_expected_identity_fails_closed(self):
        namespaces = {
            "services": "fixture-services", "observability": "fixture-observability",
            "meshFixture": "fixture-mesh", "argocdCore": "fixture-control",
        }
        item = {"kind": "Deployment", "namespace": "fixture-services",
                "name": "fixture-api", "uid": SYNTHETIC_UID}
        with self.assertRaisesRegex(capture.CaptureRejected,
                                    "duplicate-expected-identity"):
            capture.expected_identities(
                {"resources": [item, dict(item)]}, namespaces,
                {"names": {}, "prefixes": {}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
