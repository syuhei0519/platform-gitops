#!/usr/bin/env python3
"""Fail-closed execution entrance for the PLAT-213..217 fresh profile.

The module owns orchestration and checkpoints, not the cluster-specific step
implementations.  A caller must supply a finite ``StepExecutor`` adapter.  No
adapter in this repository executes kubectl, kind, Docker, or credential work.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Protocol

import yaml


HERE = Path(__file__).resolve().parent
DAG = HERE / "bootstrap-dag.yaml"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CURRENT_CLUSTER = "kind-learning-20261018-69c763"
STEPS = (
    "preflight",
    "image-source",
    "cluster",
    "empty-node-cache",
    "image-import",
    "crds",
    "controllers",
    "security-boundary",
    "credential-bootstrap",
    "custom-resources",
    "database",
    "role-bootstrap",
    "migration",
    "application",
    "acceptance",
)
NON_RECREATABLE_JOBS = {"role-bootstrap", "migration"}
GATES = ("G1", "G2", "G3", "security")
SCOPE = {
    "environment": "fresh-isolated-sandbox",
    "existingCluster": "preserve-excluded",
    "existingData": "preserve-excluded",
    "cutover": False,
}


class RunnerRejected(ValueError):
    pass


class StepExecutor(Protocol):
    """Finite adapter implemented only in the separately approved live bundle."""

    def verify(self, step: str, checkpoint: dict) -> dict: ...
    def run(self, step: str) -> dict: ...


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RunnerRejected("json-object-required")
    return value


def _load_yaml(path: Path) -> dict:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RunnerRejected("yaml-object-required")
    return value


def _parse_time(value: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise RunnerRejected(f"{field}-required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RunnerRejected(f"{field}-format") from error
    if parsed.tzinfo is None:
        raise RunnerRejected(f"{field}-timezone")
    return parsed.astimezone(timezone.utc)


def _exact_keys(value: dict, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise RunnerRejected(f"{label}-fields")


def _contains_pending(value) -> bool:
    if isinstance(value, dict):
        return any(_contains_pending(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_pending(item) for item in value)
    return isinstance(value, str) and ("PENDING_" in value or value.startswith("<operator-input:"))


def validate_dag(path: Path = DAG) -> dict:
    dag = _load_yaml(path)
    if dag.get("apiVersion") != "platform.lab/v1alpha1" or dag.get("kind") != "FreshBootstrapDAG":
        raise RunnerRejected("dag-kind")
    steps = dag.get("spec", {}).get("steps", [])
    if [item.get("id") for item in steps] != list(STEPS):
        raise RunnerRejected("dag-step-order")
    for index, item in enumerate(steps):
        expected = [] if index == 0 else [STEPS[index - 1]]
        if item.get("requires") != expected or not isinstance(item.get("readyWhen"), str) or not item["readyWhen"]:
            raise RunnerRejected(f"dag-dependency:{item.get('id')}")
    resume = dag.get("spec", {}).get("checkpoint", {}).get("resume", {})
    if resume != {
        "verifyProfileDigest": True,
        "verifyClusterUID": True,
        "verifyCompletedResources": True,
        "retryOnlyFailedOrIncompleteStep": True,
        "neverRecreateSucceededRoleBootstrapJob": True,
        "neverRecreateSucceededMigrationJob": True,
    }:
        raise RunnerRejected("dag-resume-policy")
    return dag


def validate_live_gate(approval_path: Path, materialization_path: Path,
                       profile_path: Path, now: datetime | None = None) -> dict:
    """Validate every mutation gate before a StepExecutor can be called."""
    approval_path = Path(approval_path)
    materialization_path = Path(materialization_path)
    profile_path = Path(profile_path)
    approval = _load_json(approval_path)
    materialization = _load_json(materialization_path)
    profile = _load_yaml(profile_path)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    validate_dag()

    _exact_keys(approval, {"apiVersion", "kind", "metadata", "spec"}, "approval")
    if approval["apiVersion"] != "platform.lab/v1alpha1" or approval["kind"] != "FreshExecutionApproval":
        raise RunnerRejected("approval-kind")
    if _contains_pending(approval):
        raise RunnerRejected("approval-pending-input")
    metadata = approval["metadata"]
    _exact_keys(metadata, {"name", "approvedBy", "approvedAt", "expiresAt"}, "approval-metadata")
    if metadata["name"] != "account-lightweight-fresh" or not isinstance(metadata["approvedBy"], str) or not metadata["approvedBy"].strip():
        raise RunnerRejected("approval-identity")
    approved_at = _parse_time(metadata["approvedAt"], "approved-at")
    expires_at = _parse_time(metadata["expiresAt"], "expires-at")
    if approved_at > now or expires_at <= now or expires_at <= approved_at:
        raise RunnerRejected("approval-time-window")

    spec = approval["spec"]
    _exact_keys(spec, {"approved", "target", "repositories", "hashes", "scope", "gates", "allowedSteps"},
                "approval-spec")
    if spec["approved"] is not True:
        raise RunnerRejected("approval-not-granted")
    if spec["scope"] != SCOPE:
        raise RunnerRejected("approval-scope")
    if spec["allowedSteps"] != list(STEPS):
        raise RunnerRejected("approval-steps")
    _exact_keys(spec["gates"], set(GATES), "approval-gates")
    for gate in GATES:
        decision = spec["gates"][gate]
        _exact_keys(decision, {"approved", "approvalId"}, f"approval-gate-{gate}")
        if decision["approved"] is not True or not isinstance(decision["approvalId"], str) or not decision["approvalId"].strip():
            raise RunnerRejected(f"approval-gate-{gate}")

    repositories = spec["repositories"]
    _exact_keys(repositories, {"platformGitops", "applicationManifest"}, "approval-repositories")
    if not all(SHA40.fullmatch(repositories[name] or "") for name in repositories):
        raise RunnerRejected("approval-repository-sha")
    hashes = spec["hashes"]
    _exact_keys(hashes, {"profileSha256", "materializationSha256"}, "approval-hashes")
    if not all(SHA256.fullmatch(hashes[name] or "") for name in hashes):
        raise RunnerRejected("approval-hash-format")
    if hashes["profileSha256"] != _digest(profile_path):
        raise RunnerRejected("approval-profile-hash")
    if hashes["materializationSha256"] != _digest(materialization_path):
        raise RunnerRejected("approval-materialization-hash")

    _exact_keys(materialization, {"schemaVersion", "platformGitops", "applicationManifest", "liveApproved", "files", "sourceFiles"},
                "materialization")
    if materialization["schemaVersion"] != "platform.lab/materialization/v1alpha1":
        raise RunnerRejected("materialization-schema")
    if materialization["liveApproved"] is not False:
        raise RunnerRejected("materialization-must-not-self-approve")
    if materialization["platformGitops"] != repositories["platformGitops"] or materialization["applicationManifest"] != repositories["applicationManifest"]:
        raise RunnerRejected("materialization-repositories")
    if materialization["files"].get("profile.lock.yaml") != hashes["profileSha256"]:
        raise RunnerRejected("materialization-profile-hash")
    source_files = materialization["sourceFiles"]
    if (not isinstance(source_files, dict) or not source_files
            or any(not isinstance(name, str) or not name or not SHA256.fullmatch(value or "")
                   for name, value in source_files.items())):
        raise RunnerRejected("materialization-source-files")

    if profile.get("apiVersion") != "platform.lab/v1alpha1" or profile.get("kind") != "FreshProfileLock":
        raise RunnerRejected("profile-kind")
    if _contains_pending(profile):
        raise RunnerRejected("profile-pending-input")
    if profile.get("metadata", {}).get("name") != "account-lightweight-fresh":
        raise RunnerRejected("profile-name")
    profile_spec = profile.get("spec", {})
    target = profile_spec.get("target", {})
    _exact_keys(spec["target"], {"clusterName", "context", "profileName"}, "approval-target")
    if spec["target"] != {"clusterName": target.get("clusterName"), "context": target.get("context"),
                          "profileName": "account-lightweight-fresh"}:
        raise RunnerRejected("approval-target")
    if target.get("clusterName") == CURRENT_CLUSTER or target.get("context") == CURRENT_CLUSTER:
        raise RunnerRejected("current-cluster-target")
    if target.get("isolation") != "separate-named-sandbox" or profile_spec.get("scope", {}).get("cutoverIncluded") is not False:
        raise RunnerRejected("profile-scope")
    locked = profile_spec.get("repositories", {})
    if locked.get("platform-gitops", {}).get("commit") != repositories["platformGitops"] or locked.get("application-manifest", {}).get("commit") != repositories["applicationManifest"]:
        raise RunnerRejected("profile-repositories")
    decisions = profile_spec.get("decisions", {})
    if any(decisions.get(gate, {}).get("status") != "approved" for gate in ("G1", "G2", "G3")):
        raise RunnerRejected("profile-gates")
    image_supply = profile_spec.get("imageSupply", {})
    if image_supply.get("immutable") is not True or image_supply.get("decision") == "PENDING_G2":
        raise RunnerRejected("profile-image-supply")
    return {"approval": approval, "materialization": materialization, "profile": profile}


def _new_journal(profile_digest: str, target: dict) -> dict:
    return {
        "schemaVersion": "platform.lab/fresh-run-journal/v1",
        "profileSha256": profile_digest,
        "target": {"clusterName": target["clusterName"], "context": target["context"]},
        "steps": {step: {"status": "pending", "attempts": 0, "evidence": None, "errorClass": None}
                  for step in STEPS},
    }


def _validate_journal(journal: dict, profile_digest: str, target: dict) -> None:
    _exact_keys(journal, {"schemaVersion", "profileSha256", "target", "steps"}, "journal")
    if journal["schemaVersion"] != "platform.lab/fresh-run-journal/v1" or journal["profileSha256"] != profile_digest:
        raise RunnerRejected("journal-profile")
    if journal["target"] != {"clusterName": target["clusterName"], "context": target["context"]}:
        raise RunnerRejected("journal-target")
    if list(journal["steps"]) != list(STEPS):
        raise RunnerRejected("journal-steps")
    for step, state in journal["steps"].items():
        _exact_keys(state, {"status", "attempts", "evidence", "errorClass"}, f"journal-step-{step}")
        if state["status"] not in {"pending", "running", "failed", "succeeded"}:
            raise RunnerRejected("journal-status")
        if not isinstance(state["attempts"], int) or state["attempts"] < 0:
            raise RunnerRejected("journal-attempts")


def execute(mode: str, approval_path: Path | None, materialization_path: Path | None,
            profile_path: Path, journal_path: Path, executor: StepExecutor | None,
            now: datetime | None = None) -> dict:
    """Plan or execute the finite DAG.

    Dry-run is read-only. Live performs the complete approval gate before it
    reads or writes a journal and before any executor method can be called.
    """
    if mode == "dry-run":
        return {"mode": "dry-run", "steps": list(STEPS), "mutationCalls": 0}
    if mode not in {"first", "rerun", "resume"}:
        raise RunnerRejected("mode")
    if approval_path is None or materialization_path is None or executor is None:
        raise RunnerRejected("live-inputs-required")

    sealed = validate_live_gate(approval_path, materialization_path, profile_path, now=now)
    profile_digest = _digest(Path(profile_path))
    target = sealed["profile"]["spec"]["target"]
    journal_path = Path(journal_path)
    if journal_path.exists():
        journal = _load_json(journal_path)
        _validate_journal(journal, profile_digest, target)
    else:
        if mode != "first":
            raise RunnerRejected("journal-required")
        journal = _new_journal(profile_digest, target)

    if mode == "first" and any(state["status"] != "pending" for state in journal["steps"].values()):
        raise RunnerRejected("first-run-existing-state")

    for step in STEPS:
        state = journal["steps"][step]
        if state["status"] == "succeeded":
            try:
                observed = executor.verify(step, state)
            except Exception as error:
                # Completed bootstrap and migration Jobs are evidence. Never
                # turn verification failure into implicit recreation.
                if step in NON_RECREATABLE_JOBS:
                    raise RunnerRejected(f"completed-job-verification:{step}") from error
                raise RunnerRejected(f"completed-step-verification:{step}") from error
            if not isinstance(observed, dict) or observed.get("verified") is not True:
                raise RunnerRejected(f"completed-step-verification:{step}")
            continue
        if mode == "rerun":
            raise RunnerRejected(f"rerun-incomplete-step:{step}")
        if state["status"] == "running":
            state["status"] = "failed"
            state["errorClass"] = "interrupted-before-checkpoint"
        state["status"] = "running"
        state["attempts"] += 1
        journal_path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
        try:
            evidence = executor.run(step)
            if not isinstance(evidence, dict) or evidence.get("succeeded") is not True:
                raise RunnerRejected(f"step-evidence:{step}")
        except Exception as error:
            state["status"] = "failed"
            state["errorClass"] = type(error).__name__
            journal_path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
            raise
        state.update(status="succeeded", evidence=evidence, errorClass=None)
        journal_path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
    return journal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry-run", "first", "rerun", "resume"), required=True)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--materialization", type=Path)
    parser.add_argument("--profile", type=Path, default=HERE / "profile.lock.yaml")
    parser.add_argument("--journal", type=Path, default=Path("fresh-profile-checkpoints.json"))
    args = parser.parse_args()
    if args.mode != "dry-run":
        print("live execution rejected: no cluster executor is bundled; use the separately reviewed live adapter")
        return 2
    plan = execute(args.mode, args.approval, args.materialization, args.profile, args.journal, None)
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

