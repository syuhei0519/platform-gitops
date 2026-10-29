import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import os

PATH = Path(__file__).with_name("image-supply.py")
SPEC = importlib.util.spec_from_file_location("image_supply", PATH)
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)
MIRROR_SPEC = importlib.util.spec_from_file_location("image_mirror", PATH.with_name("image-mirror.py"))
MIRROR = importlib.util.module_from_spec(MIRROR_SPEC); MIRROR_SPEC.loader.exec_module(MIRROR)


class InventoryTest(unittest.TestCase):
    def test_workflow_requires_repository_enable_gate(self):
        workflow = (PATH.parents[1] / ".github/workflows/external-image-mirror.yml").read_text()
        self.assertIn("vars.ENABLE_EXTERNAL_IMAGE_MIRROR == 'true'", workflow)
        self.assertIn("environment: ghcr-publish", workflow)
        self.assertIn("image-mirror.py", workflow)
        self.assertNotIn("crane copy", workflow)

    def test_destination_state_is_fail_closed(self):
        import subprocess
        result = lambda code=0, out="", err="": subprocess.CompletedProcess([], code, out, err)
        digest = "sha256:" + "a" * 64
        self.assertEqual(MIRROR.state(result(out=digest), digest), "same")
        self.assertEqual(MIRROR.state(result(1, err="MANIFEST_UNKNOWN"), digest), "absent")
        for value in (result(out="sha256:" + "b" * 64), result(1, err="DENIED"),
                      result(1, err="MANIFEST_UNKNOWN unauthorized"), result(1, err="timeout")):
            with self.assertRaises(MIRROR.MirrorError): MIRROR.state(value, digest)

    def test_failed_copy_is_not_retried(self):
        import subprocess
        digest = "sha256:" + "a" * 64
        responses = iter([subprocess.CompletedProcess([],0,"",""),
            subprocess.CompletedProcess([],0,digest,""),
            subprocess.CompletedProcess([],1,"","MANIFEST_UNKNOWN"),
            subprocess.CompletedProcess([],1,"","MANIFEST_UNKNOWN"),
            subprocess.CompletedProcess([],1,"","uncertain")])
        calls=[]
        def fake(args, secret=None): calls.append(args); return next(responses)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(MIRROR,"run",side_effect=fake), mock.patch.dict(os.environ,{"GITHUB_ACTOR":"actor","GH_TOKEN":"token"}), self.assertRaises(MIRROR.MirrorError):
            MIRROR.mirror("image","source", "destination", digest, Path(directory))
        self.assertEqual(sum(call[0] == "oci-copy-once" for call in calls), 1)

    def test_inventory_is_fixed_digest_and_mapped(self):
        images = MODULE.load()
        self.assertEqual(len(images), 14)
        self.assertTrue(all(i["source"].endswith("@" + i["sourceDigest"]) for i in images))
        self.assertTrue(all(i["destination"].startswith("ghcr.io/syuhei0519/platform-lab-images:") for i in images))

    def test_dispatch_selects_only_redistribution_ready_images(self):
        images = MODULE.load()
        ready = MODULE.select_ready(images, "all")
        self.assertEqual({item["name"] for item in ready}, {
            "argocd", "argo-rollouts", "istio-pilot", "istio-proxy", "prometheus",
            "prometheus-config-reloader", "otel-collector"})
        self.assertNotIn("kube-state-metrics", {item["name"] for item in ready})
        self.assertEqual([item["name"] for item in MODULE.select_ready(images, "argocd")],
            ["argocd"])
        for name in ("kube-state-metrics", "grafana", "missing"):
            with self.assertRaisesRegex(ValueError, "image-not-redistribution-ready"):
                MODULE.select_ready(images, name)

    def test_workflow_describes_all_as_ready_only(self):
        workflow = (PATH.parents[1] / ".github/workflows/external-image-mirror.yml").read_text()
        self.assertIn("確認済みのみを選ぶall", workflow)

    def test_unknown_destination_digest_is_not_present(self):
        raw = json.loads((PATH.parents[1] / "supply/images.json").read_text())
        for item in raw["images"]:
            self.assertNotIn("destinationDigest", item)
            self.assertNotIn("@sha256:", item["destinationTag"])

    def test_redistribution_evidence_is_bound_to_inventory(self):
        images = MODULE.load()
        by_name = {item["name"]: item for item in images}
        raw = json.loads((PATH.parents[1] / "supply/images.json").read_text())
        self.assertEqual(set(raw["redistributionEvidence"]), {
            "argocd", "argo-rollouts", "istio-pilot", "istio-proxy", "prometheus",
            "prometheus-config-reloader", "otel-collector"})
        self.assertNotIn("kube-state-metrics", raw["redistributionEvidence"])
        for name, relative in raw["redistributionEvidence"].items():
            evidence_path = PATH.parents[1] / relative
            evidence = json.loads(evidence_path.read_text())
            self.assertTrue(evidence["redistributionReady"])
            if "images" in evidence:
                record = next(item for item in evidence["images"] if item["name"] == name)
                self.assertEqual(record["reference"], by_name[name]["source"])
                self.assertEqual(record["manifestDigest"], by_name[name]["sourceDigest"])
                self.assertEqual(record["platform"], "linux/amd64")
                self.assertEqual(record["mediaType"],
                    "application/vnd.docker.distribution.manifest.v2+json")
            else:
                self.assertEqual(evidence["image"]["reference"], by_name[name]["source"])
                self.assertEqual(evidence["image"]["indexDigest"], by_name[name]["sourceDigest"])
            license_path = evidence_path.parent / evidence["license"]["localPath"]
            self.assertTrue(license_path.is_file())
            import hashlib
            self.assertEqual(hashlib.sha256(license_path.read_bytes()).hexdigest(),
                evidence["license"]["sha256"])
            notice = evidence["license"]["upstreamNotice"]
            if isinstance(notice, dict):
                notice_path = evidence_path.parent / notice["localPath"]
                self.assertTrue(notice_path.is_file())
                self.assertEqual(hashlib.sha256(notice_path.read_bytes()).hexdigest(),
                    notice["sha256"])

    def test_duplicate_and_mutable_source_are_refused(self):
        original = json.loads((PATH.parents[1] / "supply/images.json").read_text())
        for mutate in (lambda d: d["images"][1].update(name=d["images"][0]["name"]),
                       lambda d: d["images"][0].update(source="docker.io/kindest/node:latest")):
            data = json.loads(json.dumps(original)); mutate(data)
            with tempfile.TemporaryDirectory() as directory:
                inventory = Path(directory) / "images.json"; inventory.write_text(json.dumps(data))
                with mock.patch.object(MODULE, "INVENTORY", inventory), self.assertRaises(ValueError):
                    MODULE.load()


if __name__ == "__main__":
    unittest.main()
