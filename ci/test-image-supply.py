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
INSPECT_SPEC = importlib.util.spec_from_file_location("image_inspect", PATH.with_name("image-inspect.py"))
INSPECT = importlib.util.module_from_spec(INSPECT_SPEC); INSPECT_SPEC.loader.exec_module(INSPECT)


class InventoryTest(unittest.TestCase):
    def test_workflow_requires_repository_enable_gate(self):
        workflow = (PATH.parents[1] / ".github/workflows/external-image-mirror.yml").read_text()
        self.assertIn("vars.ENABLE_EXTERNAL_IMAGE_MIRROR == 'true'", workflow)
        self.assertIn("environment: ghcr-publish", workflow)
        self.assertIn("image-mirror.py", workflow)
        self.assertNotIn("crane copy", workflow)

    def test_inspection_job_is_read_only_and_separate_from_publish(self):
        workflow = (PATH.parents[1] / ".github/workflows/external-image-mirror.yml").read_text()
        inspect = workflow.split("  inspect:\n", 1)[1].split("  mirror:\n", 1)[0]
        self.assertIn("if: ${{ ! inputs.execute }}", inspect)
        self.assertIn("contents: read", inspect)
        self.assertIn("image-inspect.py", inspect)
        self.assertIn("python3 -m json.tool receipts/receipt.json", inspect)
        self.assertNotIn("packages: write", inspect)
        self.assertNotIn("environment:", inspect)
        self.assertNotIn("image-mirror.py", inspect)

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

    def test_read_only_inspection_can_select_one_unready_image(self):
        images = MODULE.load()
        self.assertEqual([item["name"] for item in
                          MODULE.select_for_inspection(images, "kube-state-metrics")],
                         ["kube-state-metrics"])
        self.assertEqual({item["name"] for item in
                          MODULE.select_for_inspection(images, "all")},
                         {item["name"] for item in MODULE.select_ready(images, "all")})
        with self.assertRaisesRegex(ValueError, "unknown-image"):
            MODULE.select_for_inspection(images, "missing")

    def test_inspection_records_index_child_and_config(self):
        import subprocess
        digest = "sha256:" + "a" * 64
        child = "sha256:" + "b" * 64
        config = "sha256:" + "c" * 64
        top = json.dumps({"mediaType": "index", "manifests": [{
            "digest": child, "mediaType": "manifest",
            "platform": {"os": "linux", "architecture": "amd64"}}]})
        manifest = json.dumps({"mediaType": "manifest", "config": {"digest": config}})
        config_document = json.dumps({"os": "linux", "architecture": "amd64"})
        responses = iter([
            subprocess.CompletedProcess([], 0, digest + "\n", ""),
            subprocess.CompletedProcess([], 0, top, ""),
            subprocess.CompletedProcess([], 0, child + "\n", ""),
            subprocess.CompletedProcess([], 0, manifest, ""),
            subprocess.CompletedProcess([], 0, config_document, ""),
        ])
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(INSPECT, "run", side_effect=lambda args: next(responses)):
            INSPECT.inspect("image", "registry/image:tag@" + digest, digest, Path(directory))
            receipt = json.loads((Path(directory) / "receipt.json").read_text())
        self.assertEqual(receipt["topDigest"], digest)
        self.assertEqual(receipt["selectedManifestDigest"], child)
        self.assertEqual(receipt["configDigest"], config)

    def test_single_manifest_requires_linux_amd64_config(self):
        import subprocess
        digest = "sha256:" + "a" * 64
        config = "sha256:" + "c" * 64
        manifest = json.dumps({"mediaType": "manifest", "config": {"digest": config}})
        responses = iter([
            subprocess.CompletedProcess([], 0, digest + "\n", ""),
            subprocess.CompletedProcess([], 0, manifest, ""),
            subprocess.CompletedProcess([], 0,
                                        json.dumps({"os": "linux", "architecture": "arm64"}), ""),
        ])
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(INSPECT, "run", side_effect=lambda args: next(responses)), \
                self.assertRaisesRegex(INSPECT.InspectError, "config platform mismatch"):
            INSPECT.inspect("image", "registry/image:tag@" + digest, digest, Path(directory))

    def test_index_descriptor_media_type_must_match_child(self):
        import subprocess
        digest = "sha256:" + "a" * 64
        child = "sha256:" + "b" * 64
        config = "sha256:" + "c" * 64
        top = json.dumps({"mediaType": "index", "manifests": [{
            "digest": child, "mediaType": "unexpected",
            "platform": {"os": "linux", "architecture": "amd64"}}]})
        manifest = json.dumps({"mediaType": "manifest", "config": {"digest": config}})
        responses = iter([
            subprocess.CompletedProcess([], 0, digest + "\n", ""),
            subprocess.CompletedProcess([], 0, top, ""),
            subprocess.CompletedProcess([], 0, child + "\n", ""),
            subprocess.CompletedProcess([], 0, manifest, ""),
        ])
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(INSPECT, "run", side_effect=lambda args: next(responses)), \
                self.assertRaisesRegex(INSPECT.InspectError, "child media type mismatch"):
            INSPECT.inspect("image", "registry/image:tag@" + digest, digest, Path(directory))

    def test_inspection_fails_closed_on_digest_mismatch(self):
        import subprocess
        digest = "sha256:" + "a" * 64
        result = subprocess.CompletedProcess([], 0, "sha256:" + "b" * 64, "")
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(INSPECT, "run", return_value=result), \
                self.assertRaisesRegex(INSPECT.InspectError, "source digest mismatch"):
            INSPECT.inspect("image", "registry/image:tag@" + digest, digest, Path(directory))

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

    def test_delivered_consumers_are_ghcr_pinned_and_pending_are_absent(self):
        images = MODULE.load()
        by_name = {item["name"]: item for item in images}
        raw = json.loads((PATH.parents[1] / "supply/images.json").read_text())
        self.assertEqual(set(raw["deliveries"]), set(raw["redistributionEvidence"]))
        for name, delivery in raw["deliveries"].items():
            item = by_name[name]
            self.assertEqual(delivery["destinationDigest"], item["sourceDigest"])
            for relative in delivery["consumerFiles"]:
                text = (PATH.parents[1] / relative).read_text()
                self.assertIn("ghcr.io/syuhei0519/platform-lab-images", text)
                self.assertIn(delivery["destinationDigest"], text)
        self.assertTrue({"kind-node", "argocd-redis", "postgres-18", "grafana",
                         "kube-state-metrics", "gitlab-runner",
                         "gitlab-runner-helper"}.isdisjoint(raw["deliveries"]))

    def test_account_canonical_normal_consumer_is_public_ghcr(self):
        canonical = ("ghcr.io/syuhei0519/backend-app@sha256:"
                     "fba8eae42982a0e818581b2ac6b41ba865920dafdbfd7d48c416f98c5b1eaebd")
        profile = (PATH.parents[1] / "learning/fresh-profile/profile.template.yaml").read_text()
        contract = json.loads((PATH.parents[1] /
            "learning/fresh-profile/security/canonical-contract.json").read_text())
        self.assertIn(canonical, profile)
        self.assertEqual(contract["approvedRuntimeImage"], canonical)

    def test_pending_redistribution_materials_cannot_enter_ready_matrix(self):
        images = MODULE.load()
        by_name = {item["name"]: item for item in images}
        raw = json.loads((PATH.parents[1] / "supply/images.json").read_text())
        preparations = {
            "kind-node": "kind-node/v1.36.4",
            "argocd-redis": "argocd-redis/8.6.4-alpine",
            "postgres-18": "postgres/18.6-bookworm",
            "gitlab-runner": "gitlab-runner/v19.3.1",
            "gitlab-runner-helper": "gitlab-runner-helper/x86_64-v19.3.1",
        }
        self.assertTrue(set(preparations).isdisjoint(raw["redistributionEvidence"]))
        for name, relative in preparations.items():
            directory = PATH.parents[1] / "supply/licenses" / relative
            record = json.loads((directory / "preparation.json").read_text())
            self.assertEqual(record["schemaVersion"],
                             "platform.lab/redistribution-preparation/v1")
            self.assertEqual(record["name"], name)
            self.assertIs(record["redistributionReady"], False)
            self.assertEqual(record["copyMode"], "oci-byte-preserving")
            self.assertEqual(record["image"]["reference"], by_name[name]["source"])
            self.assertIn(by_name[name]["sourceDigest"], {
                record["image"].get("indexDigest"),
                record["image"].get("manifestDigest"),
            })
            self.assertEqual(record["image"]["selectedPlatform"], "linux/amd64")
            self.assertIsNone(record["image"]["selectedManifestDigest"])
            self.assertIsNone(record["image"]["configDigest"])
            self.assertTrue(record["missingEvidence"])
            license_path = directory / record["license"]["localPath"]
            self.assertTrue(license_path.is_file())
            import hashlib
            self.assertEqual(hashlib.sha256(license_path.read_bytes()).hexdigest(),
                             record["license"]["sha256"])

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
