#!/usr/bin/env python3
"""Validate PLAT-217 evidence records without performing live operations."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path

from jsonschema import Draft7Validator


HERE = Path(__file__).resolve().parent
RECORD = HERE / "acceptance.pending.json"
SCHEMA = HERE / "acceptance.schema.json"
PROFILE = HERE / "profile.lock.yaml"
CURRENT_CLUSTER = "kind-learning-20261018-69c763"
RUN_MODES = ["first", "rerun", "resume-after-interruption"]
STEPS = [
    "preflight", "image-supply", "cluster", "crds", "controllers",
    "security-boundary", "credential-bootstrap", "custom-resources",
    "database", "role-bootstrap", "migration", "application", "acceptance",
]
READINESS = {
    "root-nonempty", "dependencies-ready", "workloads-ready", "api",
    "metrics", "traces", "telemetry-profile-and-pod-uid", "actual-db-role-owner",
}
RESYNC_RESTART = {"argocd-resync", "workload-restart", "db-down-recovery", "old-image-rollback"}
NEGATIVE_CASES = {
    "missing-secret", "wrong-secret", "wrong-architecture",
    "unready-dependency", "healthy-root-empty-child", "wrong-profile-identity",
}
FORBIDDEN_SECRET_KEYS = {
    "data", "stringData", "password", "token", "privateKey",
    "secretValue", "credential",
}

PROFILE_MODULE = HERE / "validate_profile.py"
SPEC = importlib.util.spec_from_file_location("validate_profile", PROFILE_MODULE)
profile_contract = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(profile_contract)


class AcceptanceError(ValueError):
    pass


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reject_secret_material(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_SECRET_KEYS:
                raise AcceptanceError("secret-material-key")
            reject_secret_material(child)
    elif isinstance(value, list):
        for child in value:
            reject_secret_material(child)
    elif isinstance(value, str):
        if "-----BEGIN " in value or value.startswith("Bearer "):
            raise AcceptanceError("secret-material-value")


def exact_ids(items, expected, error):
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise AcceptanceError(error)


def require_passed(checks, error):
    if any(item["status"] != "passed" or not item["detail"] for item in checks):
        raise AcceptanceError(error)


def validate_runs(runs, complete):
    if [run["mode"] for run in runs] != RUN_MODES:
        raise AcceptanceError("run-modes")
    valid_steps = set(STEPS)
    for run in runs:
        if not set(run["completedSteps"]).issubset(valid_steps):
            raise AcceptanceError("run-step")
        if not set(run["retriedSteps"]).issubset(valid_steps):
            raise AcceptanceError("run-retry-step")
        if run["migrationJobRecreated"]:
            raise AcceptanceError("migration-job-recreated")
    first, rerun, resume = runs
    if first["status"] == "passed" and (first["completedSteps"] != STEPS or first["verifiedOnly"]):
        raise AcceptanceError("first-run")
    if first["status"] == "passed" and (first["interruptedStep"] is not None or first["resumedAtStep"] is not None or first["retriedSteps"]):
        raise AcceptanceError("first-run-resume-fields")
    if rerun["status"] == "passed" and (rerun["completedSteps"] != STEPS or not rerun["verifiedOnly"]):
        raise AcceptanceError("rerun-not-idempotent")
    if rerun["status"] == "passed" and (rerun["interruptedStep"] is not None or rerun["resumedAtStep"] is not None or rerun["retriedSteps"]):
        raise AcceptanceError("rerun-resume-fields")
    interrupted = resume["interruptedStep"]
    if resume["status"] == "passed" and (interrupted not in STEPS or resume["resumedAtStep"] != interrupted):
        raise AcceptanceError("resume-boundary")
    if resume["status"] == "passed" and (resume["completedSteps"] != STEPS or resume["verifiedOnly"]):
        raise AcceptanceError("resume-completion")
    if resume["status"] == "passed" and resume["retriedSteps"] != STEPS[STEPS.index(interrupted):]:
        raise AcceptanceError("resume-retried-steps")
    if complete and any(run["status"] != "passed" for run in runs):
        raise AcceptanceError("run-incomplete")


def validate(record_path: Path = RECORD, require_complete: bool = False,
             profile_path: Path = PROFILE):
    record = load_json(record_path)
    schema = load_json(SCHEMA)
    errors = sorted(Draft7Validator(schema).iter_errors(record), key=lambda error: list(error.path))
    if errors:
        raise AcceptanceError("schema:" + "/".join(map(str, errors[0].path)))
    reject_secret_material(record)

    profile, _, _ = profile_contract.validate(profile_path, require_approved=require_complete)
    spec = profile["spec"]
    if record["profile"]["sha256"] != file_sha256(profile_path):
        raise AcceptanceError("profile-digest")
    repositories = {name: value["commit"] for name, value in spec["repositories"].items()}
    if record["profile"]["repositories"] != repositories:
        raise AcceptanceError("repository-revisions")
    target = record["target"]
    if target["clusterName"] != spec["target"]["clusterName"] or target["context"] != spec["target"]["context"]:
        raise AcceptanceError("target-identity")
    if target["kubeconfigRef"] != spec["inputs"]["clusterKubeconfigRef"]:
        raise AcceptanceError("kubeconfig-reference")
    if target["architecture"] != spec["target"]["architecture"]:
        raise AcceptanceError("target-architecture")
    if target["clusterName"] == CURRENT_CLUSTER or target["context"] == CURRENT_CLUSTER:
        raise AcceptanceError("current-cluster-target")

    expected_images = {}
    for item in spec["images"]:
        expected_images[item["name"]] = {
            "digest": item["ref"].rsplit("@", 1)[1],
            "architecture": item["architecture"],
        }
    images = record["evidence"]["images"]
    if len(images) != len(expected_images) or {item["name"] for item in images} != set(expected_images):
        raise AcceptanceError("image-set")
    for item in images:
        expected = expected_images[item["name"]]
        if item["expectedDigest"] != expected["digest"] or item["expectedArchitecture"] != expected["architecture"]:
            raise AcceptanceError("image-lock")

    evidence = record["evidence"]
    if evidence["revisions"]["desired"] != repositories:
        raise AcceptanceError("desired-revisions")
    exact_ids(evidence["readiness"], READINESS, "readiness-set")
    exact_ids(evidence["resyncRestart"], RESYNC_RESTART, "resync-restart-set")
    exact_ids(record["negativeCases"], NEGATIVE_CASES, "negative-case-set")
    if record["preservation"]["currentCluster"]["id"] != "existing-cluster-unchanged":
        raise AcceptanceError("current-preservation-id")
    if record["preservation"]["existingData"]["id"] != "existing-data-unchanged":
        raise AcceptanceError("data-preservation-id")
    validate_runs(record["runs"], complete=require_complete)

    # A partially collected live record may mix passed, failed and unrun
    # sections. Every section marked passed must already be self-consistent;
    # only --require-complete additionally requires that all sections passed.
    for item in images:
        if item["status"] == "passed" and (
                item["retrievedDigest"] != item["expectedDigest"]
                or item["actualArchitecture"] != item["expectedArchitecture"]
                or item["sourceType"] not in {"private-registry", "retained-oci-archive"}):
            raise AcceptanceError("image-observed")
    resources = evidence["resources"]
    if resources["status"] == "passed" and set(resources["observed"]) != set(resources["expected"]):
        raise AcceptanceError("resources-observed")
    revisions = evidence["revisions"]
    if revisions["status"] == "passed" and revisions["actual"] != revisions["desired"]:
        raise AcceptanceError("revisions-observed")
    ownership = evidence["fieldOwnership"]
    if ownership["status"] == "passed" and set(ownership["observed"]) != set(ownership["expected"]):
        raise AcceptanceError("field-ownership")
    for item in evidence["readiness"] + evidence["resyncRestart"]:
        if item["status"] == "passed" and not item["detail"]:
            raise AcceptanceError("passed-check-without-detail")
    for item in record["negativeCases"]:
        if item["status"] == "passed" and item["observedClass"] != item["expectedClass"]:
            raise AcceptanceError("negative-result")
    for item in (record["preservation"]["currentCluster"], record["preservation"]["existingData"]):
        if item["status"] == "passed" and not item["detail"]:
            raise AcceptanceError("passed-preservation-without-detail")

    live = record["metadata"]["liveEvidence"]
    if not live:
        if record["metadata"]["recordedAt"] is not None or target["clusterUID"] is not None:
            raise AcceptanceError("unrun-live-identity")
        statuses = [run["status"] for run in record["runs"]]
        statuses += [item["status"] for item in images]
        statuses += [evidence["resources"]["status"], evidence["revisions"]["status"], evidence["fieldOwnership"]["status"]]
        statuses += [item["status"] for item in evidence["readiness"] + evidence["resyncRestart"]]
        statuses += [item["status"] for item in record["negativeCases"]]
        statuses += [record["preservation"]["currentCluster"]["status"], record["preservation"]["existingData"]["status"]]
        if any(status != "unrun" for status in statuses):
            raise AcceptanceError("unrun-claims-result")
        if any(run["completedSteps"] or run["retriedSteps"] or run["interruptedStep"] or run["resumedAtStep"] for run in record["runs"]):
            raise AcceptanceError("unrun-claims-journal")
        if evidence["resources"]["observed"] or evidence["revisions"]["actual"] or evidence["fieldOwnership"]["observed"]:
            raise AcceptanceError("unrun-claims-observation")
        if any(item["retrievedDigest"] or item["actualArchitecture"] or item["sourceType"] != "unrun" for item in images):
            raise AcceptanceError("unrun-claims-image")
        if any(item["detail"] for item in evidence["readiness"] + evidence["resyncRestart"]):
            raise AcceptanceError("unrun-claims-detail")
        if any(item["observedClass"] for item in record["negativeCases"]):
            raise AcceptanceError("unrun-claims-negative")

    if require_complete:
        if not live or not record["metadata"]["recordedAt"] or not target["clusterUID"]:
            raise AcceptanceError("complete-live-identity")
        try:
            recorded_at = datetime.fromisoformat(record["metadata"]["recordedAt"].replace("Z", "+00:00"))
            if recorded_at.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError as error:
            raise AcceptanceError("recorded-at") from error
        for item in images:
            if (item["status"] != "passed" or item["retrievedDigest"] != item["expectedDigest"]
                    or item["actualArchitecture"] != item["expectedArchitecture"]
                    or item["sourceType"] not in {"private-registry", "retained-oci-archive"}):
                raise AcceptanceError("image-observed")
        if resources["status"] != "passed" or set(resources["observed"]) != set(resources["expected"]):
            raise AcceptanceError("resources-observed")
        if revisions["status"] != "passed" or revisions["actual"] != revisions["desired"]:
            raise AcceptanceError("revisions-observed")
        require_passed(evidence["readiness"], "readiness-incomplete")
        if ownership["status"] != "passed" or set(ownership["observed"]) != set(ownership["expected"]):
            raise AcceptanceError("field-ownership")
        require_passed(evidence["resyncRestart"], "resync-restart-incomplete")
        for item in record["negativeCases"]:
            if item["status"] != "passed" or item["observedClass"] != item["expectedClass"]:
                raise AcceptanceError("negative-result")
        preservation = record["preservation"]
        require_passed([preservation["currentCluster"], preservation["existingData"]], "preservation-incomplete")
        if preservation["cleanupStatus"] not in {"not-requested", "passed"}:
            raise AcceptanceError("cleanup-incomplete")
    return record, profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--profile", type=Path, default=PROFILE)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        record, _ = validate(args.record, args.require_complete, args.profile)
    except (AcceptanceError, profile_contract.ContractError, json.JSONDecodeError) as error:
        print(f"fresh acceptance rejected: {error}")
        return 1
    print("fresh acceptance valid",
          f"live={str(record['metadata']['liveEvidence']).lower()}",
          f"runs={len(record['runs'])}",
          f"images={len(record['evidence']['images'])}",
          f"resources={len(record['evidence']['resources']['expected'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
