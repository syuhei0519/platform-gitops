import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import os
from datetime import datetime, timezone

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
        self.assertIn("scan-evidence", workflow)
        self.assertIn("aquasec/trivy:0.75.0@sha256:9db099105405c648166e6b94155eb32f8da12673cf1f455207f7385cc9a77283", inspect)
        self.assertIn("--format cyclonedx", inspect)
        self.assertIn(".security/scan-gate -policy security/scan-policy.json", inspect)
        self.assertIn("-scanner-exit 0", inspect)
        self.assertIn("--input .oci/layout", inspect)
        self.assertIn("-image-config \"$CONFIG_DIGEST\"", inspect)
        self.assertIn("oci-crane-check .oci/layout \"$SELECTED_DIGEST\"", inspect)
        self.assertIn("Bind an approved candidate to one immutable digest", inspect)
        self.assertIn("crane digest \"$SOURCE\"", inspect)
        self.assertIn("inputs.candidate == 'none'", workflow)

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
            "argocd", "argocd-redis", "argo-rollouts", "istio-pilot", "istio-proxy", "prometheus",
            "prometheus-config-reloader", "otel-collector"})
        self.assertNotIn("kube-state-metrics", {item["name"] for item in ready})
        self.assertEqual([item["name"] for item in MODULE.select_ready(images, "argocd")],
            ["argocd"])
        for name in ("kube-state-metrics", "grafana", "missing"):
            with self.assertRaisesRegex(ValueError, "image-not-redistribution-ready"):
                MODULE.select_ready(images, name)

    def test_redis_scan_freshness_is_rechecked_at_write_time(self):
        redis = MODULE.select_ready(MODULE.load(), "argocd-redis")
        MODULE.require_fresh_scan(
            redis, datetime(2026, 10, 31, 13, 6, 27, tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError, "scan-db-expired"):
            MODULE.require_fresh_scan(
                redis, datetime(2026, 10, 31, 13, 6, 29, tzinfo=timezone.utc))
        workflow = (PATH.parents[1] /
                    ".github/workflows/external-image-mirror.yml").read_text()
        mirror = workflow.split("  mirror:\n", 1)[1]
        self.assertIn("Recheck scan database freshness immediately before copy", mirror)
        self.assertIn("--require-fresh-scan", mirror)
        self.assertLess(mirror.index("--require-fresh-scan"),
                        mirror.index("image-mirror.py"))

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

    def test_candidate_inspection_is_static_and_non_publishable(self):
        runner = MODULE.select_candidate("gitlab-runner-v19.3.5")
        helper = MODULE.select_candidate("gitlab-runner-helper-v19.3.5")
        alpine_helper = MODULE.select_candidate(
            "gitlab-runner-helper-alpine-v19.3.5")
        self.assertEqual(runner[0]["source"],
                         "registry.gitlab.com/gitlab-org/gitlab-runner:alpine-v19.3.5")
        self.assertEqual(helper[0]["source"],
                         "registry.gitlab.com/gitlab-org/ci-cd/gitlab-runner-ubi-images/"
                         "gitlab-runner-helper-ocp:x86_64-v19.3.5")
        self.assertEqual(alpine_helper[0]["source"],
                         "registry.gitlab.com/gitlab-org/gitlab-runner/"
                         "gitlab-runner-helper:x86_64-v19.3.5")
        for candidate in (runner[0], helper[0], alpine_helper[0]):
            self.assertIs(candidate["candidate"], True)
            self.assertEqual(candidate["sourceDigest"], "")
            self.assertEqual(candidate["destination"],
                             "read-only-candidate-not-publishable")
        with self.assertRaisesRegex(ValueError, "unknown-candidate"):
            MODULE.select_candidate("arbitrary-registry-input")

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

    def test_inspection_failure_is_safely_classified(self):
        import subprocess
        cases = (
            ("unexpected status code 401 Unauthorized", "http-401"),
            ("request failed: status=403", "http-403"),
            ("GET: HTTP/1.1 404 Not Found", "http-404"),
            ("registry response status code: 429", "http-429"),
            ("registry response status code: 503", "http-5xx"),
            ("context deadline exceeded", "timeout"),
            ("unsupported media type application/example", "unsupported-media"),
            ("lookup registry.invalid: no such host", "dns"),
            ("tls: failed to verify certificate: x509: certificate signed by unknown authority",
             "tls"),
            ("read: connection reset by peer", "connection"),
            ("reading response body: unexpected EOF", "eof"),
            ("registry response: MANIFEST_UNKNOWN", "manifest-unknown"),
            ("token=secret-403-value https://user:password@example.invalid?q=secret",
             "unknown"),
            ("GET https://user:password@example.invalid/?status=403&token=secret failed",
             "unknown"),
            ("GET https://example.invalid/?error=manifest_unknown&token=secret failed",
             "unknown"),
            ("token=connection-reset secret=unexpected-EOF", "unknown"),
        )
        for stderr, category in cases:
            result = subprocess.CompletedProcess([], 17, "", stderr)
            with self.subTest(category=category), \
                    mock.patch.object(INSPECT, "run", return_value=result), \
                    self.assertRaises(INSPECT.InspectError) as raised:
                INSPECT.checked(["crane", "manifest", "private-ref"],
                                "top manifest unavailable")
            message = str(raised.exception)
            self.assertEqual(
                message,
                "top manifest unavailable (crane_exit=17; category=" + category + ")")
            self.assertNotIn(stderr, message)
            for secret in ("secret", "password", "private-ref", "example.invalid"):
                self.assertNotIn(secret, message)

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
            "argocd", "argocd-redis", "argo-rollouts", "istio-pilot", "istio-proxy", "prometheus",
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
                    "application/vnd.oci.image.manifest.v1+json" if name == "argocd-redis"
                    else "application/vnd.docker.distribution.manifest.v2+json")
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
        self.assertTrue(set(raw["deliveries"]).issubset(raw["redistributionEvidence"]))
        self.assertEqual(set(raw["redistributionEvidence"]) - set(raw["deliveries"]),
                         {"argocd-redis"})
        for name, delivery in raw["deliveries"].items():
            item = by_name[name]
            self.assertEqual(delivery["destinationDigest"], item["sourceDigest"])
            for consumer in raw["consumerReferences"][name]:
                actual = MODULE._consumer_image(
                    PATH.parents[1] / consumer["path"], consumer["type"],
                    consumer["identity"])
                self.assertIn(actual, {
                    "ghcr.io/syuhei0519/platform-lab-images:"
                    + item["destinationTag"] + "@" + delivery["destinationDigest"],
                    "ghcr.io/syuhei0519/platform-lab-images@"
                    + delivery["destinationDigest"],
                })
        self.assertTrue({"kind-node", "postgres-18", "grafana",
                         "kube-state-metrics", "gitlab-runner",
                         "gitlab-runner-helper"}.isdisjoint(raw["deliveries"]))

    def test_upstream_or_unknown_consumer_digest_is_rejected(self):
        upstream = ("quay.io/argoproj/argocd:v3.5.2@sha256:"
                    "478701ca1208502315ae55eecb50180292739f6b579d4bbfa9015a43e7c71e68")
        unknown = ("ghcr.io/syuhei0519/platform-lab-images@sha256:"
                   + "d" * 64)
        real_consumer = MODULE._consumer_image
        for actual in (upstream, unknown):
            def changed(path, consumer_type, identity):
                if identity == "argocd":
                    return actual
                return real_consumer(path, consumer_type, identity)
            with self.subTest(actual=actual), \
                    mock.patch.object(MODULE, "_consumer_image", side_effect=changed), \
                    self.assertRaisesRegex(ValueError,
                                           "delivery-consumer-reference:argocd"):
                MODULE.load()

    def test_undelivered_grafana_profile_and_lock_cannot_move_to_unknown_ghcr(self):
        original = json.loads((PATH.parents[1] / "supply/images.json").read_text())
        unknown_digest = "sha256:" + "e" * 64
        unknown = "ghcr.io/syuhei0519/platform-lab-images@" + unknown_digest
        grafana = next(item for item in original["images"] if item["name"] == "grafana")
        grafana["source"] = "docker.io/grafana/grafana:13.2.3@" + unknown_digest
        actual_consumer = MODULE._consumer_image
        def changed(path, consumer_type, identity):
            if identity in {"grafana", "spec.plannedObservability.grafana.image"}:
                return unknown
            return actual_consumer(path, consumer_type, identity)
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "images.json"
            inventory.write_text(json.dumps(original))
            with mock.patch.object(MODULE, "INVENTORY", inventory), \
                    mock.patch.object(MODULE, "_consumer_image", side_effect=changed), \
                    self.assertRaisesRegex(ValueError,
                                           "undelivered-consumer-reference:grafana"):
                MODULE.load()

    def test_account_canonical_normal_consumer_is_public_ghcr(self):
        canonical = ("ghcr.io/syuhei0519/backend-app@sha256:"
                     "fba8eae42982a0e818581b2ac6b41ba865920dafdbfd7d48c416f98c5b1eaebd")
        profile = (PATH.parents[1] / "learning/fresh-profile/profile.template.yaml").read_text()
        contract = json.loads((PATH.parents[1] /
            "learning/fresh-profile/security/canonical-contract.json").read_text())
        self.assertIn(canonical, profile)
        self.assertEqual(contract["approvedRuntimeImage"], canonical)
        inventory = json.loads((PATH.parents[1] / "supply/images.json").read_text())
        self.assertEqual(inventory["accountDelivery"], {
            "runUrl": "https://github.com/syuhei0519/backend-app/actions/runs/40924904358",
            "destination": "ghcr.io/syuhei0519/backend-app",
            "destinationDigest": canonical.rpartition("@")[2],
        })

    def test_delivery_run_or_account_receipt_tamper_is_refused(self):
        original = json.loads((PATH.parents[1] / "supply/images.json").read_text())
        mutations = (
            lambda data: data["deliveries"]["argocd"].update(
                runUrl="https://github.com/syuhei0519/platform-gitops/actions/runs/1"),
            lambda data: data["accountDelivery"].update(
                runUrl="https://github.com/syuhei0519/backend-app/actions/runs/1"),
        )
        for mutate in mutations:
            data = json.loads(json.dumps(original)); mutate(data)
            with tempfile.TemporaryDirectory() as directory:
                inventory = Path(directory) / "images.json"
                inventory.write_text(json.dumps(data))
                with mock.patch.object(MODULE, "INVENTORY", inventory), \
                        self.assertRaises(ValueError):
                    MODULE.load()

    def test_pending_redistribution_materials_cannot_enter_ready_matrix(self):
        images = MODULE.load()
        by_name = {item["name"]: item for item in images}
        raw = json.loads((PATH.parents[1] / "supply/images.json").read_text())
        preparations = {
            "kind-node": "kind-node/v1.36.4",
            "postgres-18": "postgres/18.6-bookworm",
            "gitlab-runner": "gitlab-runner/v19.3.1",
            "gitlab-runner-helper": "gitlab-runner-helper/x86_64-v19.3.1",
        }
        inspection_runs = {
            "kind-node": 41016047353,
            "postgres-18": 41020934558,
            "gitlab-runner": 41021024706,
            "gitlab-runner-helper": 41021115023,
        }
        inspected_identities = {
            "kind-node": ("sha256:597367624b4748b74b98e4fe2d661cd78063d02ecdaca702fd90572375b83e67", "sha256:0fa975026f71246c4e4744409d43e2e5884bb6e960851b438e50f455808c16f4"),
            "postgres-18": ("sha256:6b38fe68f2c02cafffbbce7496f76d110d8a947c039bd22c067e9a43c2250240", "sha256:63d4ad6f3de5e288238b3044970a677dcb0e77004d8b6050732aa0cbbeedf5d7"),
            "gitlab-runner": ("sha256:f5578027351e1db11564fff9333977fb0d8d13c802b35d42a2f392872fec6a30", "sha256:a69c746c42524ae219ae3e8a41c0848c8990d1a30ef3d5eedf659a78f316d0b2"),
            "gitlab-runner-helper": ("sha256:ebcce5b4e36899e72aa20554962d7270a0eec1e398e1b0a0f746ede4f4bb1b70", "sha256:1523809e0948018b4c25badacfd4b80dc1607fbc27a10af4298acafd512d1552"),
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
            self.assertEqual((record["image"]["selectedManifestDigest"],
                              record["image"]["configDigest"]),
                             inspected_identities[name])
            receipt = record["inspectionReceipt"]
            self.assertEqual(receipt["provenance"], "user-provided-run-log")
            self.assertEqual(receipt["schemaVersion"],
                             "platform.lab/image-inspection-receipt/v1")
            self.assertEqual(receipt["runUrl"],
                             "https://github.com/syuhei0519/platform-gitops/actions/runs/"
                             + str(inspection_runs[name]))
            self.assertNotIn("linux-amd64-manifest-and-config-receipt",
                             record["missingEvidence"])
            scan = record["scanEvidence"]
            self.assertEqual(scan["provenance"],
                             "user-provided-github-actions-artifact")
            self.assertIs(scan["pass"], False)
            self.assertTrue(scan["criticalFindings"])
            self.assertIn("critical-vulnerabilities-must-be-fixed",
                          record["missingEvidence"])
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
