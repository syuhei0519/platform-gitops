#!/usr/bin/env python3

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import unittest

import yaml


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
LEGACY_RUNTIME_IDENTITY_PATH = "docs/implementation/issue-17-slo-alerting.md"
LEGACY_RUNTIME_IDENTITY_GIT_BLOB = "816ef6301acac272c569c867351817def83a1544"
LEGACY_RUNTIME_IDENTITY_SHA256 = (
    "71db5e2f46f5eb3f5c719781f8d1e9440dfc03b64af332226d8d9a4f206bd1cf")
UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE)
PERSONAL_PATH = re.compile(r"(?:[A-Za-z]:\\Users\\|\\\\wsl\.localhost\\)")
HOME_PATH = re.compile("/" + "home" + r"/([^/\s]+)")
ALLOWED_SERVICE_HOMES = {"argocd", "argo-rollouts", "user"}
ENVIRONMENT_NAME = re.compile(
    r"(?:learning-services-[0-9]{8}|learning-[0-9]{8}-[0-9a-f]+)",
    re.IGNORECASE)
RENDER_SPEC = importlib.util.spec_from_file_location(
    "existing_kind_render", HERE / "render.py")
render = importlib.util.module_from_spec(RENDER_SPEC)
assert RENDER_SPEC.loader is not None
RENDER_SPEC.loader.exec_module(render)


def git_blob_sha1(data):
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def is_exact_legacy_runtime_identity_document(relative, data):
    return (
        relative == LEGACY_RUNTIME_IDENTITY_PATH
        and git_blob_sha1(data) == LEGACY_RUNTIME_IDENTITY_GIT_BLOB
        and hashlib.sha256(data).hexdigest() == LEGACY_RUNTIME_IDENTITY_SHA256)


def public_identity_violations(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    violations = []
    if UUID.search(text):
        violations.append("uuid")
    if PERSONAL_PATH.search(text):
        violations.append("personal-path")
    if any(
            match.group(1) not in ALLOWED_SERVICE_HOMES
            for match in HOME_PATH.finditer(text)):
        violations.append("home-path")
    if ENVIRONMENT_NAME.search(text):
        violations.append("environment-name")
    return violations


class PublicRuntimeSeparationTests(unittest.TestCase):
    def test_entire_tracked_tree_contains_no_runtime_identity_or_personal_path(self):
        tracked = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=REPOSITORY, check=True,
            capture_output=True).stdout.decode().split("\0")
        for relative in tracked:
            if not relative:
                continue
            path = REPOSITORY / relative
            if not path.is_file():
                continue
            data = path.read_bytes()
            if is_exact_legacy_runtime_identity_document(relative, data):
                continue
            self.assertEqual(public_identity_violations(data), [], str(path))

    def test_legacy_runtime_identity_exception_is_exact_and_immutable(self):
        path = REPOSITORY / LEGACY_RUNTIME_IDENTITY_PATH
        data = path.read_bytes()
        self.assertTrue(is_exact_legacy_runtime_identity_document(
            LEGACY_RUNTIME_IDENTITY_PATH, data))
        self.assertFalse(is_exact_legacy_runtime_identity_document(
            "docs/implementation/another-document.md", data))
        mutated = bytes([data[0] ^ 1]) + data[1:]
        self.assertFalse(is_exact_legacy_runtime_identity_document(
            LEGACY_RUNTIME_IDENTITY_PATH, mutated))
        self.assertIn("environment-name", public_identity_violations(mutated))

    def test_runtime_directories_are_ignored_and_untracked(self):
        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPOSITORY, check=True,
            capture_output=True).stdout.decode().split("\0")
        self.assertFalse(any("learning/existing-kind/.runtime/" in item for item in tracked))
        self.assertFalse(any("learning/sonarqube/.runtime/" in item for item in tracked))
        for candidate in (
                HERE / ".runtime" / "inventory.runtime.local.json",
                REPOSITORY / "learning" / "sonarqube" / ".runtime" / "evidence.json"):
            result = subprocess.run(
                ["git", "check-ignore", "--no-index", "--quiet", str(candidate)],
                cwd=REPOSITORY, check=False)
            self.assertEqual(result.returncode, 0, str(candidate))

    def test_public_inventory_files_are_empty_synthetic_templates(self):
        for name, collection in (
                ("inventory.template.json", "resources"),
                ("ownership.template.json", "resources"),
                ("pod-baseline.template.json", "pods")):
            value = json.loads((HERE / name).read_text(encoding="utf-8"))
            self.assertIs(value["synthetic"], True)
            self.assertEqual(value[collection], [])
            self.assertTrue(value["context"].startswith("<runtime-input:"))
        for retired in ("inventory.json", "ownership.json", "pod-baseline.json"):
            self.assertFalse((HERE / retired).exists())

    def test_public_identity_inputs_are_templates_not_runtime_evidence(self):
        for name in (
                "namespace-identities.template.json",
                "crd-contracts.template.json",
                "pv-identities.template.json"):
            self.assertTrue((HERE / "inputs" / name).is_file())
        for retired in (
                "namespace-identities.json", "crd-contracts.json",
                "pv-identities.json"):
            self.assertFalse((HERE / "inputs" / retired).exists())
        def keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)
        for name in (
                "namespace-identities.template.json",
                "crd-contracts.template.json",
                "pv-identities.template.json"):
            value = json.loads((HERE / "inputs" / name).read_text(encoding="utf-8"))
            self.assertNotIn("uid", set(keys(value)), name)
        identity_template = json.loads(
            (HERE / "adoption" / "identities.template.json").read_text(
                encoding="utf-8"))
        self.assertIs(identity_template["synthetic"], True)
        self.assertEqual(identity_template["identities"], [])
        self.assertFalse((HERE / "adoption" / "identities.json").exists())
        pv_decision = json.loads(
            (HERE / "proposal" / "existing-pv-decision.template.json").read_text(
                encoding="utf-8"))
        self.assertIs(pv_decision["synthetic"], True)
        self.assertEqual(pv_decision["action"], "runtime-decision-required")
        self.assertFalse((HERE / "proposal" / "existing-pv-decision.json").exists())

    def test_public_client_is_non_applicable_synthetic_template(self):
        path = HERE / "proposal" / "account-client.template.yaml"
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(value["kind"], "SyntheticManifestTemplate")
        self.assertEqual(value["metadata"]["annotations"]["platform.lab/synthetic"], "true")
        self.assertTrue(all(
            item.startswith("<runtime-input:")
            for item in value["spec"]["runtimeInputs"].values()))
        self.assertFalse((HERE / "proposal" / "account-client.yaml").exists())
        checksums = json.loads(
            (HERE / "proposal" / "checksums.json").read_text(encoding="utf-8"))
        expected = checksums["account-client.template.yaml"]
        self.assertEqual(expected["bytes"], len(path.read_bytes()))
        self.assertEqual(expected["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        generated = yaml.safe_dump_all([render.account_client_template()], sort_keys=False)
        self.assertEqual(generated.encode(), path.read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
