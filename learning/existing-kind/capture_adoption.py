#!/usr/bin/env python3
"""Capture reviewed public parents using explicit Git-ignored runtime evidence.

The command is read-only against Kubernetes. It refuses checked-in target,
inventory and output paths, implicit contexts and changed resource identities.
"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]
RUNTIME_ROOT = ROOT / ".runtime"
KINDS = {
    "Deployment", "StatefulSet", "Service", "ConfigMap",
    "PersistentVolumeClaim", "ServiceAccount",
}
RUNTIME_KIND = "ExistingKindCaptureRuntime"
GROUPS = {"services", "observability", "meshFixture", "argocdCore"}
UID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
STATIC_SKIPS = {"default", "kube-root-ca.crt", "istio-ca-crl"}


class CaptureRejected(ValueError):
    pass


def runtime_file(path: Path) -> Path:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise CaptureRejected("runtime-file")
    if path.is_relative_to(REPOSITORY) and not path.is_relative_to(RUNTIME_ROOT):
        raise CaptureRejected("tracked-runtime-input")
    return path


def runtime_output(path: Path) -> Path:
    path = path.resolve()
    if path.exists():
        raise CaptureRejected("new-output-required")
    if path.is_relative_to(REPOSITORY) and not path.is_relative_to(RUNTIME_ROOT):
        raise CaptureRejected("tracked-runtime-output")
    if not path.parent.is_dir():
        raise CaptureRejected("output-parent-required")
    return path


def load_json(path: Path) -> dict:
    try:
        value = json.loads(runtime_file(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CaptureRejected("runtime-json") from error
    if not isinstance(value, dict):
        raise CaptureRejected("runtime-json-object")
    return value


def load_runtime(contract_path: Path, inventory_path: Path, context: str):
    contract = load_json(contract_path)
    inventory = load_json(inventory_path)
    try:
        target = contract["target"]
        namespaces = contract["namespaces"]
        exclusions = contract["exclusions"]
    except KeyError as error:
        raise CaptureRejected("runtime-contract-shape") from error
    if (contract.get("apiVersion") != "platform.lab/v1alpha1"
            or contract.get("kind") != RUNTIME_KIND
            or set(namespaces) != GROUPS
            or not all(isinstance(value, str) and value for value in namespaces.values())
            or target.get("context") != context
            or not isinstance(target.get("kubeSystemUid"), str)
            or inventory.get("context") != context
            or not isinstance(inventory.get("resources"), list)
            or inventory.get("synthetic") is True):
        raise CaptureRejected("runtime-contract")
    runtime_values = [context, target["kubeSystemUid"], *namespaces.values()]
    if (any(value.startswith("<runtime-input:") for value in runtime_values)
            or UID.fullmatch(target["kubeSystemUid"]) is None):
        raise CaptureRejected("runtime-placeholder")
    if (set(exclusions) != {"names", "prefixes"}
            or not isinstance(exclusions["names"], dict)
            or not isinstance(exclusions["prefixes"], dict)):
        raise CaptureRejected("runtime-exclusions")
    for table in exclusions.values():
        if (any(namespace not in namespaces.values() for namespace in table)
                or any(not isinstance(items, list)
                       or not all(isinstance(item, str) and item for item in items)
                       for items in table.values())):
            raise CaptureRejected("runtime-exclusions")
    return target, namespaces, exclusions, inventory


def expected_identities(inventory: dict, namespaces: dict,
                        exclusions: dict) -> dict:
    """Return the complete reviewed identity set that capture must observe."""
    expected = {}
    namespace_set = set(namespaces.values())
    for item in inventory["resources"]:
        try:
            key = (item["kind"], item.get("namespace", ""), item["name"])
            uid = item["uid"]
        except (KeyError, TypeError) as error:
            raise CaptureRejected("inventory-identity-shape") from error
        if not isinstance(uid, str) or UID.fullmatch(uid) is None:
            raise CaptureRejected("inventory-identity-shape")
        if key in expected:
            raise CaptureRejected("duplicate-expected-identity")
        expected[key] = uid

    scoped = {}
    for key, uid in expected.items():
        kind, namespace, name = key
        if kind == "CustomResourceDefinition" and namespace == "":
            scoped[key] = uid
            continue
        if kind not in KINDS or namespace not in namespace_set:
            continue
        skip_names = STATIC_SKIPS | set(exclusions["names"].get(namespace, []))
        skip_prefixes = tuple(exclusions["prefixes"].get(namespace, []))
        if name in skip_names or name.startswith(skip_prefixes):
            continue
        scoped[key] = uid
    return scoped


def record_observed(expected: dict, observed: dict, key: tuple, uid: str,
                    mismatch: str) -> None:
    if key in observed:
        raise CaptureRejected("duplicate-observed-identity")
    if key not in expected:
        raise CaptureRejected("unexpected-observed-identity")
    if expected[key] != uid:
        raise CaptureRejected(mismatch)
    observed[key] = uid


def require_complete_identity_set(expected: dict, observed: dict) -> None:
    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    if missing or extra:
        raise CaptureRejected(
            f"identity-set-mismatch:missing={missing}:extra={extra}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubectl", type=Path, required=True)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--runtime-contract", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    kubectl = runtime_file(args.kubectl)
    kubeconfig = runtime_file(args.kubeconfig)
    output = runtime_output(args.output)
    target, namespaces, exclusions, inventory = load_runtime(
        args.runtime_contract, args.inventory, args.context)

    def get(kind, namespace=None, name=None):
        command = [str(kubectl), "--kubeconfig", str(kubeconfig), "--context",
                   args.context, "get", kind]
        if name:
            command.append(name)
        if namespace:
            command += ["-n", namespace]
        command += ["-o", "json"]
        completed = subprocess.run(command, check=False, capture_output=True,
                                   text=True, stdin=subprocess.DEVNULL)
        if completed.returncode != 0:
            raise CaptureRejected("kubectl-exit:" + str(completed.returncode))
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise CaptureRejected("kubectl-json") from error

    if get("namespace", name="kube-system")["metadata"]["uid"] != target["kubeSystemUid"]:
        raise CaptureRejected("cluster-identity-mismatch")
    expected = expected_identities(inventory, namespaces, exclusions)
    observed = {}
    group_namespaces = {
        "services": namespaces["services"],
        "observability": namespaces["observability"],
        "mesh-fixture": namespaces["meshFixture"],
        "argocd-core": namespaces["argocdCore"],
    }
    result = {}
    identities = []
    for group, namespace in group_namespaces.items():
        documents = []
        skip_names = STATIC_SKIPS | set(exclusions["names"].get(namespace, []))
        skip_prefixes = tuple(exclusions["prefixes"].get(namespace, []))
        for kind in sorted(KINDS):
            for live in get(kind, namespace)["items"]:
                metadata = live["metadata"]
                name = metadata["name"]
                if name in skip_names or name.startswith(skip_prefixes):
                    continue
                key = (kind, namespace, name)
                record_observed(expected, observed, key, metadata["uid"],
                                "parent-identity-changed")
                document = {key: deepcopy(live[key]) for key in ("apiVersion", "kind")}
                document["metadata"] = {
                    key: deepcopy(metadata[key])
                    for key in ("name", "namespace", "labels") if key in metadata
                }
                annotations = {
                    key: value for key, value in metadata.get("annotations", {}).items()
                    if key not in {"kubectl.kubernetes.io/last-applied-configuration",
                                   "deployment.kubernetes.io/revision"}
                }
                annotations["argocd.argoproj.io/sync-options"] = "Prune=false,Delete=false"
                document["metadata"]["annotations"] = annotations
                for field in ("spec", "data", "binaryData", "automountServiceAccountToken",
                              "imagePullSecrets"):
                    if field in live:
                        document[field] = deepcopy(live[field])
                for container in document.get("spec", {}).get("template", {}).get(
                        "spec", {}).get("containers", []):
                    for env in container.get("env", []):
                        if (re.search("PASSWORD|TOKEN|SECRET|LICENSE_KEY", env["name"])
                                and "value" in env
                                and not env["value"].startswith("/")):
                            raise CaptureRejected("literal-credential-like-environment")
                serialized = yaml.safe_dump(document)
                if ("-----BEGIN PRIVATE KEY-----" in serialized
                        or "-----BEGIN RSA PRIVATE KEY-----" in serialized):
                    raise CaptureRejected("private-key-refused")
                documents.append(document)
                identities.append({
                    "kind": kind, "namespace": namespace, "name": name,
                    "uid": metadata["uid"],
                    "sha256": hashlib.sha256(json.dumps(
                        document, sort_keys=True).encode()).hexdigest(),
                })
        result[group + ".yaml"] = documents

    crds = []
    for live in get("crd")["items"]:
        metadata = live["metadata"]
        key = ("CustomResourceDefinition", "", metadata["name"])
        record_observed(expected, observed, key, metadata["uid"],
                        "crd-identity-changed")
        crds.append({
            "apiVersion": live["apiVersion"], "kind": live["kind"],
            "metadata": {"name": metadata["name"], "annotations": {
                "argocd.argoproj.io/sync-options": "Prune=false,Delete=false"}},
            "spec": live["spec"],
        })
    result["crds.yaml"] = crds
    require_complete_identity_set(expected, observed)

    output.mkdir()
    for name, documents in result.items():
        (output / name).write_text(
            yaml.safe_dump_all(documents, sort_keys=False), encoding="utf-8")
    (output / "identities.json").write_text(
        json.dumps(identities, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: len(documents) for name, documents in result.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
