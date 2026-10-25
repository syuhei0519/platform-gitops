#!/usr/bin/env python3
"""Validate the offline fresh-profile lock without touching a cluster."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml
from jsonschema import Draft7Validator


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
PROFILE = HERE / "profile.lock.yaml"
SCHEMA = HERE / "profile.schema.json"
CURRENT_CLUSTER = "kind-learning-20261018-69c763"
REQUIRED_REPOSITORIES = {
    "backend-app", "frontend-app", "application-manifest", "platform-gitops"
}
REQUIRED_IMAGES = {
    "kind-node", "argocd", "argocd-redis", "argo-rollouts", "istio-pilot",
    "istio-proxy", "postgres", "account-runtime", "prometheus",
    "otel-collector", "grafana",
}
DIGEST_ONLY_IMAGES = {"argocd", "argocd-redis", "prometheus", "otel-collector", "grafana"}
STEPS = [
    "preflight", "image-source", "cluster", "empty-node-cache", "image-import", "crds", "controllers",
    "security-boundary", "credential-bootstrap", "custom-resources",
    "database", "role-bootstrap", "migration", "application", "acceptance",
]
REQUIRES = {
    "preflight": [], "image-source": ["preflight"], "cluster": ["image-source"],
    "empty-node-cache": ["cluster"], "image-import": ["empty-node-cache"],
    "crds": ["image-import"], "controllers": ["crds"],
    "security-boundary": ["controllers"], "credential-bootstrap": ["security-boundary"],
    "custom-resources": ["credential-bootstrap"], "database": ["custom-resources"],
    "role-bootstrap": ["database"], "migration": ["role-bootstrap"],
    "application": ["migration"], "acceptance": ["application"],
}
STOP_CONDITIONS = {
    "host-memory-below-minimum", "host-disk-below-minimum",
    "docker-disk-above-maximum", "elapsed-budget-exceeded",
    "pod-pending-budget-exceeded", "unexpected-restart",
}
FORBIDDEN_SECRET_KEYS = {
    "data", "stringData", "password", "token", "secretValue", "privateKey",
}


class ContractError(ValueError):
    pass


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def checked_path(value: str) -> Path:
    path = (REPOSITORY / value).resolve()
    if not path.is_relative_to(REPOSITORY.resolve()) or not path.is_file():
        raise ContractError("referenced-file")
    return path


def digest_only(reference: str) -> str:
    """Remove a mutable tag while preserving repository and manifest digest."""
    repository, separator, digest = reference.rpartition("@")
    if not separator or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ContractError("image-digest")
    leaf = repository.rsplit("/", 1)[-1]
    if ":" in leaf:
        repository = repository.rsplit(":", 1)[0]
    return repository + "@" + digest


def reject_secret_material(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_SECRET_KEYS:
                raise ContractError("secret-material-key")
            reject_secret_material(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secret_material(child, path + (str(index),))
    elif isinstance(value, str):
        if "-----BEGIN " in value or value.startswith("Bearer "):
            raise ContractError("secret-material-value")


def validate_dag(dag):
    steps = dag["spec"]["steps"]
    if [step["id"] for step in steps] != STEPS:
        raise ContractError("dag-order")
    if any(step["requires"] != REQUIRES[step["id"]] for step in steps):
        raise ContractError("dag-dependency")
    checkpoint = dag["spec"]["checkpoint"]
    if (not checkpoint["recordAfterEveryStep"]
            or not checkpoint["resume"]["neverRecreateSucceededRoleBootstrapJob"]
            or not checkpoint["resume"]["neverRecreateSucceededMigrationJob"]):
        raise ContractError("checkpoint-resume")


def validate_equivalence(equivalence):
    expected_tickets = {"PLAT-177", *(f"PLAT-{number}" for number in range(193, 200))}
    if {item["ticket"] for item in equivalence["mappings"]} != expected_tickets:
        raise ContractError("equivalence-tickets")
    if any(not item["freshChecks"] for item in equivalence["mappings"]):
        raise ContractError("equivalence-empty")


def validate(profile_path: Path = PROFILE, require_approved: bool = False):
    profile = load_yaml(profile_path)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(Draft7Validator(schema).iter_errors(profile), key=lambda e: list(e.path))
    if errors:
        raise ContractError("schema:" + "/".join(map(str, errors[0].path)))
    spec = profile["spec"]
    reject_secret_material(profile)

    if set(spec["repositories"]) != REQUIRED_REPOSITORIES:
        raise ContractError("repository-set")
    revisions = {name: repo["commit"] for name, repo in spec["repositories"].items()}
    if any(not re.fullmatch(r"[0-9a-f]{40}", revision)
           for name, revision in revisions.items() if name != "platform-gitops"):
        raise ContractError("repository-sha")
    if revisions["platform-gitops"] != "__PLATFORM_GITOPS_SHA__" and not re.fullmatch(
            r"[0-9a-f]{40}", revisions["platform-gitops"]):
        raise ContractError("repository-sha")

    target = spec["target"]
    if target["clusterName"] == CURRENT_CLUSTER or target["context"] == "kind-" + CURRENT_CLUSTER.removeprefix("kind-"):
        raise ContractError("current-cluster-target")
    names = [image["name"] for image in spec["images"]]
    if len(names) != len(set(names)) or set(names) != REQUIRED_IMAGES:
        raise ContractError("image-set")
    if any(image["architecture"] != target["architecture"] for image in spec["images"]):
        raise ContractError("image-architecture")
    pending_runtime = next(image for image in spec["images"] if image["name"] == "account-runtime")["ref"]
    if pending_runtime != "PENDING_PLAT211_VERIFIED_IMAGE" and not re.fullmatch(
            r"ghcr\.io/syuhei0519/backend-app@sha256:[0-9a-f]{64}", pending_runtime):
        raise ContractError("account-runtime-image")

    supply = spec["imageSupply"]
    if spec["decisions"]["G2"]["status"] == "pending-approval":
        if supply["decision"] != "PENDING_G2" or supply["immutable"]:
            raise ContractError("pending-g2-supply")
    elif supply["decision"] not in {"private-registry", "retained-oci-archive",
                                    "public-upstream-plus-retained-account-archive"} or not supply["immutable"]:
        raise ContractError("approved-g2-supply")

    if set(spec["budget"]["stopOn"]) != STOP_CONDITIONS:
        raise ContractError("stop-budget")
    limits = spec["budget"]["approvalLimits"]
    if spec["decisions"]["G1"]["status"] == "pending-approval":
        if any(value != "PENDING_G1" for key, value in limits.items()
               if key not in {"imageBytesMaxGiB", "unexpectedRestartsMax"}):
            raise ContractError("pending-g1-budget")
    if spec["decisions"]["G2"]["status"] == "pending-approval" and limits["imageBytesMaxGiB"] != "PENDING_G2":
        raise ContractError("pending-g2-budget")
    if spec["decisions"]["G4"] != {
        "status": "pending-cutover", "choice": "not-required-for-fresh-acceptance"
    }:
        raise ContractError("g4-boundary")

    if require_approved:
        for gate in ("G1", "G2", "G3"):
            if spec["decisions"][gate]["status"] != "approved":
                raise ContractError("approval-" + gate.lower())
        if spec["freshRuntimeRequirement"]["admissionSecretKeyContract"] != \
                "username-password-operation-key-allowlist-aligned":
            raise ContractError("approved-admission-secret-key-contract")
        pending_fields = [target["clusterName"], target["context"], supply["decision"],
                          supply["retentionUntil"], supply["restoreOwner"]]
        pending_fields += [item[field] for item in spec["inputs"]["secrets"]
                           for field in ("owner", "expiry", "sourceRef")]
        if any(str(value).startswith("PENDING_") for value in pending_fields):
            raise ContractError("approved-profile-placeholder")
        if any(not item["sourceRef"].startswith("sealed-input:sha256:")
               for item in spec["inputs"]["secrets"]):
            raise ContractError("approved-secret-source-ref")
        if revisions["platform-gitops"] == "__PLATFORM_GITOPS_SHA__":
            raise ContractError("approved-profile-revision-token")
        if pending_runtime == "PENDING_PLAT211_VERIFIED_IMAGE":
            raise ContractError("approved-account-runtime-pending")
        if any(str(value).startswith("PENDING_") for value in limits.values()):
            raise ContractError("approved-budget-placeholder")

    dag = load_yaml(checked_path(spec["bootstrap"]["dagFile"]))
    validate_dag(dag)

    equivalence = json.loads(checked_path(spec["contracts"]["equivalence"]).read_text(encoding="utf-8"))
    validate_equivalence(equivalence)
    checked_path(spec["contracts"]["currentComparison"])
    checked_path(spec["contracts"]["secretContract"])
    checked_path(spec["contracts"]["imageSupply"])

    version_lock = load_yaml(REPOSITORY / "bootstrap" / "versions.lock.yaml")["spec"]
    expected = {
        "kind-node": version_lock["kind"]["nodeImage"],
        "argocd": digest_only(version_lock["argoCd"]["image"]),
        "argocd-redis": digest_only(version_lock["argoCd"]["redisImage"]),
        "prometheus": digest_only(version_lock["prometheus"]["image"]),
        "otel-collector": digest_only(version_lock["plannedObservability"]["collectorContrib"]["image"]),
        "grafana": digest_only(version_lock["plannedObservability"]["grafana"]["image"]),
    }
    locked_images = {item["name"]: item["ref"] for item in spec["images"]}
    if any(locked_images[name] != image for name, image in expected.items()):
        raise ContractError("version-lock-image")
    tool_pairs = {
        "kind": version_lock["kind"]["version"], "kubectl": version_lock["kubectl"],
        "helm": version_lock["helm"], "argoCdChart": version_lock["argoCd"]["version"],
        "argoCd": version_lock["argoCd"]["appVersion"],
    }
    if any(spec["tools"][name] != version for name, version in tool_pairs.items()):
        raise ContractError("version-lock-tool")
    return profile, dag, equivalence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=PROFILE)
    parser.add_argument("--require-approved", action="store_true")
    args = parser.parse_args()
    try:
        profile, dag, equivalence = validate(args.profile, args.require_approved)
    except (ContractError, yaml.YAMLError, json.JSONDecodeError) as error:
        print(f"fresh profile rejected: {error}")
        return 1
    print("fresh profile valid",
          f"repositories={len(profile['spec']['repositories'])}",
          f"images={len(profile['spec']['images'])}",
          f"steps={len(dag['spec']['steps'])}",
          f"comparisons={len(equivalence['mappings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

