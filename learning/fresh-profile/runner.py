#!/usr/bin/env python3
"""Fail-closed execution entrance for the PLAT-213..217 fresh profile.

The module owns orchestration and checkpoints. Live mode uses a separately
approved, hash-bound finite command plan through ``approved_live_adapter``.
Nothing is executed by materialization, validation, or dry-run.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from typing import Protocol

import yaml


HERE = Path(__file__).resolve().parent
DAG = HERE / "bootstrap-dag.yaml"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SOURCE_KEY = re.compile(r"^(platform-gitops|application-manifest)@([0-9a-f]{40}):(.+)$")
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
REQUIRED_SEALED_FILES = {
    "profile.lock.yaml", "image-supply.lock.yaml", "bootstrap.contract.yaml",
    "bootstrap-dag.yaml",
    "acceptance.pending.json",
    "authority-matrix.json", "fixtures/canonical-two-sha.json",
    "fixtures/security-negative-fixtures.json", "root/bootstrap.yaml",
    "root/children/applications.yaml", "security/admission.yaml",
    "security/canonical-contract.json", "security/rbac.yaml",
}
REQUIRED_SOURCE_PATHS = {
    "platform-gitops": {
        "learning/fresh-profile/root/children/applications.yaml",
        "learning/fresh-profile/observability/resources.yaml",
    },
    "application-manifest": {
        "learning/fresh-profile/release-definition/Chart.yaml",
        "learning/fresh-profile/release-definition/values.yaml",
        "learning/fresh-profile/release-definition/templates/application.yaml",
        "learning/fresh-profile/workload/account-role-bootstrap.sql",
        "learning/fresh-profile/workload/account.yaml",
        "learning/fresh-profile/workload/db-permission-cases.json",
        "learning/fresh-profile/workload/kustomization.yaml",
    },
}
TOOL_VERSION_KEYS = {
    "kind": "kind", "kubectl": "kubectl", "helm": "helm", "istioctl": "istio",
    "argocd": "argoCd", "rollouts": "rollouts",
}


class RunnerRejected(ValueError):
    pass


class StepExecutor(Protocol):
    """Approved live adapter. Methods may mutate only from ``run``."""

    plan_sha256: str
    def identity(self, target: dict) -> dict: ...
    def verify(self, step: str, checkpoint: dict) -> dict: ...
    def reconcile(self, step: str, checkpoint: dict) -> dict: ...
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
    return isinstance(value, str) and (
        "PENDING_" in value or value.startswith("<runtime-input:"))


def _operator_inputs(value, found=None):
    found = found if found is not None else set()
    if isinstance(value, dict):
        for child in value.values():
            _operator_inputs(child, found)
    elif isinstance(value, list):
        for child in value:
            _operator_inputs(child, found)
    elif isinstance(value, str) and value.startswith("<operator-input:") and value.endswith(">"):
        found.add(value)
    return found


def _mapping_digest(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _node_import_image_digests(supply_lock: dict) -> set[str]:
    """Return only images imported into the kind node after it is created.

    ``kind-node`` is the image used by ``kind create cluster`` for the outer
    Docker container.  It is bound separately by ``kindNodeRef`` and is not a
    workload image expected in that node's CRI image inventory.
    """
    images = supply_lock["spec"]["images"]
    if sum(item.get("name") == "kind-node" for item in images) != 1:
        raise RunnerRejected("image-supply-kind-node")
    digests = {
        item["source"]["manifest"].get(
            "childDigest", item["source"]["manifest"].get("manifestDigest"))
        for item in images if item["name"] != "kind-node"
    }
    if not digests or None in digests:
        raise RunnerRejected("image-supply-node-import-digests")
    return digests


def _safe_file(root: Path, relative: str, label: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise RunnerRejected(label)
    return candidate


def _load_contract_module(name: str):
    path = HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"fresh_runner_{name}", path)
    if spec is None or spec.loader is None:
        raise RunnerRejected("contract-loader:" + name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_sealed_files(materialization: dict, bundle_root: Path) -> None:
    files = materialization.get("files")
    if (not isinstance(files, dict) or not REQUIRED_SEALED_FILES.issubset(files)
            or any(not isinstance(name, str) or not SHA256.fullmatch(value or "")
                   for name, value in files.items())):
        raise RunnerRejected("materialization-files")
    for relative, expected in files.items():
        if _digest(_safe_file(bundle_root, relative, "materialization-file-path")) != expected:
            raise RunnerRejected("materialization-file-digest:" + relative)


def _validate_source_files(materialization: dict, repositories: dict,
                           platform_source_root: Path, application_source_root: Path) -> str:
    source_files = materialization.get("sourceFiles")
    if (not isinstance(source_files, dict) or not source_files
            or any(not isinstance(name, str) or not SHA256.fullmatch(value or "")
                   for name, value in source_files.items())):
        raise RunnerRejected("materialization-source-files")
    roots = {"platform-gitops": Path(platform_source_root),
             "application-manifest": Path(application_source_root)}
    repository_revisions = {
        "platform-gitops": repositories["platformGitops"],
        "application-manifest": repositories["applicationManifest"],
    }
    expected_identities = {
        f"{repository}@{repository_revisions[repository]}:{relative}"
        for repository, paths in REQUIRED_SOURCE_PATHS.items()
        for relative in paths
    }
    if set(source_files) != expected_identities:
        raise RunnerRejected("materialization-source-set")
    for identity, expected in source_files.items():
        match = SOURCE_KEY.fullmatch(identity)
        if match is None:
            raise RunnerRejected("source-file-identity")
        repository, revision, relative = match.groups()
        if revision != repository_revisions[repository]:
            raise RunnerRejected("source-file-revision")
        if _digest(_safe_file(roots[repository], relative, "source-file-path")) != expected:
            raise RunnerRejected("source-file-digest:" + identity)
    return _mapping_digest(source_files)


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
        "verifyMaterializationDigest": True,
        "verifySourceSetDigest": True,
        "verifyLiveAdapterInputSetDigest": True,
        "verifyInputReferenceDigests": True,
        "verifyClusterUID": True,
        "verifyCompletedResources": True,
        "retryOnlyFailedOrIncompleteStep": True,
        "neverRecreateSucceededRoleBootstrapJob": True,
        "neverRecreateSucceededMigrationJob": True,
    }:
        raise RunnerRejected("dag-resume-policy")
    return dag


def validate_live_gate(approval_path: Path, materialization_path: Path,
                       profile_path: Path, platform_source_root: Path,
                       application_source_root: Path, adapter_plan_path: Path,
                       now: datetime | None = None) -> dict:
    """Validate every mutation gate before a StepExecutor can be called."""
    approval_path = Path(approval_path)
    materialization_path = Path(materialization_path)
    profile_path = Path(profile_path)
    approval = _load_json(approval_path)
    materialization = _load_json(materialization_path)
    profile = _load_yaml(profile_path)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
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
    _exact_keys(spec, {"approved", "target", "repositories", "hashes", "scope", "gates",
                       "inputDigests", "allowedSteps"},
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
    _exact_keys(hashes, {"profileSha256", "materializationSha256", "sourceSetSha256",
                         "liveAdapterPlanSha256", "liveAdapterInputSetSha256"},
                "approval-hashes")
    if not all(SHA256.fullmatch(hashes[name] or "") for name in hashes):
        raise RunnerRejected("approval-hash-format")
    if hashes["profileSha256"] != _digest(profile_path):
        raise RunnerRejected("approval-profile-hash")
    if hashes["materializationSha256"] != _digest(materialization_path):
        raise RunnerRejected("approval-materialization-hash")
    if hashes["liveAdapterPlanSha256"] != _digest(Path(adapter_plan_path)):
        raise RunnerRejected("approval-live-adapter-plan-hash")

    _exact_keys(materialization, {"schemaVersion", "platformGitops", "applicationManifest", "liveApproved", "files", "sourceFiles"},
                "materialization")
    if materialization["schemaVersion"] != "platform.lab/materialization/v1alpha1":
        raise RunnerRejected("materialization-schema")
    if materialization["liveApproved"] is not False:
        raise RunnerRejected("materialization-must-not-self-approve")
    if materialization["platformGitops"] != repositories["platformGitops"] or materialization["applicationManifest"] != repositories["applicationManifest"]:
        raise RunnerRejected("materialization-repositories")
    bundle_root = materialization_path.parent.resolve()
    _validate_sealed_files(materialization, bundle_root)
    validate_dag(_safe_file(bundle_root, "bootstrap-dag.yaml", "sealed-bootstrap-dag"))
    if materialization["files"].get("profile.lock.yaml") != hashes["profileSha256"]:
        raise RunnerRejected("materialization-profile-hash")
    source_set_digest = _validate_source_files(materialization, repositories,
                                               platform_source_root, application_source_root)
    if hashes["sourceSetSha256"] != source_set_digest:
        raise RunnerRejected("approval-source-set-hash")

    if profile.get("apiVersion") != "platform.lab/v1alpha1" or profile.get("kind") != "FreshProfileLock":
        raise RunnerRejected("profile-kind")
    if _contains_pending(profile):
        raise RunnerRejected("profile-pending-input")
    if profile.get("metadata", {}).get("name") != "account-lightweight-fresh":
        raise RunnerRejected("profile-name")
    try:
        profile_contract = _load_contract_module("validate_profile")
        profile_contract.validate(profile_path, require_approved=True)
    except Exception as error:
        raise RunnerRejected("profile-contract:" + str(error)) from error
    profile_spec = profile.get("spec", {})
    target = profile_spec.get("target", {})
    _exact_keys(spec["target"], {"clusterName", "context", "profileName"}, "approval-target")
    if spec["target"] != {"clusterName": target.get("clusterName"), "context": target.get("context"),
                          "profileName": "account-lightweight-fresh"}:
        raise RunnerRejected("approval-target")
    protected_cluster = profile_spec.get("scope", {}).get("currentClusterExcluded")
    if (target.get("clusterName") == protected_cluster
            or target.get("context") == protected_cluster
            or target.get("context") == "kind-" + str(protected_cluster).removeprefix("kind-")):
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
    image_supply_path = _safe_file(bundle_root, "image-supply.lock.yaml", "image-supply-file")
    try:
        supply_contract = _load_contract_module("validate_image_supply")
        # Before cluster creation only immutable source/archive provenance can
        # exist. Empty-node-cache import/pull evidence is a post-cluster step.
        supply_lock, _ = supply_contract.validate(
            image_supply_path, require_source_evidence=True,
            profile_override=profile_path)
    except Exception as error:
        raise RunnerRejected("image-supply-contract:" + str(error)) from error

    secrets = profile_spec.get("inputs", {}).get("secrets", [])
    expected_secrets = {
        ("account-db-bootstrap", "fresh-account/account-db-bootstrap"),
        ("account-db-runtime", "fresh-account/account-db-runtime"),
        ("account-db-migration", "fresh-account/account-db-migration"),
    }
    if {(item.get("name"), item.get("destination")) for item in secrets} != expected_secrets:
        raise RunnerRejected("g3-secret-set")
    for item in secrets:
        if (not isinstance(item.get("owner"), str) or not item["owner"].strip()
                or item["owner"].startswith("PENDING_")):
            raise RunnerRejected("g3-owner")
        try:
            expiry = datetime.fromisoformat(item["expiry"] + "T23:59:59+00:00")
        except (KeyError, TypeError, ValueError) as error:
            raise RunnerRejected("g3-expiry") from error
        if expiry <= now:
            raise RunnerRejected("g3-expired")
        if not re.fullmatch(r"sealed-input:sha256:[0-9a-f]{64}", item.get("sourceRef", "")):
            raise RunnerRejected("g3-source-ref")

    inputs = profile_spec.get("inputs", {})
    expected_input_refs = {inputs.get("clusterKubeconfigRef"), inputs.get("imageSupplyRef")}
    expected_input_refs.update(item["sourceRef"] for item in secrets)
    input_digests = spec["inputDigests"]
    if (None in expected_input_refs or not isinstance(input_digests, dict)
            or set(input_digests) != expected_input_refs
            or any(not SHA256.fullmatch(value or "") for value in input_digests.values())):
        raise RunnerRejected("approval-input-digests")
    for reference, digest_value in input_digests.items():
        if reference.startswith("sealed-input:sha256:") and reference.rsplit(":", 1)[1] != digest_value:
            raise RunnerRejected("g3-input-digest")
    expected_versions = {
        alias: profile_spec.get("tools", {}).get(profile_key)
        for alias, profile_key in TOOL_VERSION_KEYS.items()
    }
    try:
        adapter_contract = _load_contract_module("approved_live_adapter")
        plan = adapter_contract.validate_plan(
            Path(adapter_plan_path),
            {"clusterName": target["clusterName"], "context": target["context"]},
            expected_versions,
            _node_import_image_digests(supply_lock),
            adapter_contract.supply_plan_contract(supply_lock, profile_spec),
        )
        live_input_set_digest = adapter_contract.plan_input_set_digest(plan)
        if hashes["liveAdapterInputSetSha256"] != live_input_set_digest:
            raise RunnerRejected("approval-live-adapter-input-set-hash")
    except Exception as error:
        raise RunnerRejected("live-adapter-contract:" + str(error)) from error
    return {"approval": approval, "materialization": materialization, "profile": profile,
            "sourceSetSha256": source_set_digest, "inputDigests": input_digests,
            "liveAdapterPlanSha256": hashes["liveAdapterPlanSha256"],
            "liveAdapterInputSetSha256": live_input_set_digest}


def _new_journal(profile_digest: str, materialization_digest: str, source_set_digest: str,
                 live_adapter_plan_digest: str, live_adapter_input_set_digest: str,
                 input_digests: dict, target: dict,
                 cluster_uid: str | None) -> dict:
    return {
        "schemaVersion": "platform.lab/fresh-run-journal/v3",
        "profileSha256": profile_digest,
        "materializationSha256": materialization_digest,
        "sourceSetSha256": source_set_digest,
        "liveAdapterPlanSha256": live_adapter_plan_digest,
        "liveAdapterInputSetSha256": live_adapter_input_set_digest,
        "inputDigests": input_digests,
        "target": {"clusterName": target["clusterName"], "context": target["context"],
                   "clusterUID": cluster_uid},
        "steps": {step: {"status": "pending", "attempts": 0, "evidence": None,
                          "resourceDigest": None, "errorClass": None}
                  for step in STEPS},
    }


def _validate_journal(journal: dict, profile_digest: str, materialization_digest: str,
                      source_set_digest: str, live_adapter_plan_digest: str,
                      live_adapter_input_set_digest: str,
                      input_digests: dict, target: dict, observed_identity: dict) -> None:
    _exact_keys(journal, {"schemaVersion", "profileSha256", "materializationSha256",
                          "sourceSetSha256", "liveAdapterPlanSha256", "inputDigests",
                          "liveAdapterInputSetSha256",
                          "target", "steps"}, "journal")
    if journal["schemaVersion"] != "platform.lab/fresh-run-journal/v3" or journal["profileSha256"] != profile_digest:
        raise RunnerRejected("journal-profile")
    if journal["materializationSha256"] != materialization_digest:
        raise RunnerRejected("journal-materialization")
    if (journal["sourceSetSha256"] != source_set_digest
            or journal["liveAdapterPlanSha256"] != live_adapter_plan_digest
            or journal["liveAdapterInputSetSha256"] != live_adapter_input_set_digest
            or journal["inputDigests"] != input_digests):
        raise RunnerRejected("journal-inputs")
    if journal["target"].get("clusterName") != target["clusterName"] or journal["target"].get("context") != target["context"]:
        raise RunnerRejected("journal-target")
    if observed_identity.get("context") != target["context"]:
        raise RunnerRejected("observed-context")
    expected_uid = journal["target"].get("clusterUID")
    if expected_uid is not None and observed_identity.get("clusterUID") != expected_uid:
        raise RunnerRejected("journal-cluster-uid")
    if list(journal["steps"]) != list(STEPS):
        raise RunnerRejected("journal-steps")
    for step, state in journal["steps"].items():
        _exact_keys(state, {"status", "attempts", "evidence", "resourceDigest", "errorClass"},
                    f"journal-step-{step}")
        if state["status"] not in {"pending", "running", "uncertain", "failed", "succeeded"}:
            raise RunnerRejected("journal-status")
        if not isinstance(state["attempts"], int) or state["attempts"] < 0:
            raise RunnerRejected("journal-attempts")
        if state["status"] == "succeeded" and not SHA256.fullmatch(state.get("resourceDigest") or ""):
            raise RunnerRejected("journal-resource-digest")


def execute(mode: str, approval_path: Path | None, materialization_path: Path | None,
            profile_path: Path, journal_path: Path, executor: StepExecutor | None,
            platform_source_root: Path | None = None,
            application_source_root: Path | None = None,
            adapter_plan_path: Path | None = None,
            now: datetime | None = None) -> dict:
    """Plan or execute the finite DAG.

    Dry-run is read-only. Live performs the complete approval gate before it
    reads or writes a journal and before any executor method can be called.
    """
    if mode == "dry-run":
        return {"mode": "dry-run", "steps": list(STEPS), "mutationCalls": 0}
    if mode not in {"first", "rerun", "resume"}:
        raise RunnerRejected("mode")
    if adapter_plan_path is None and materialization_path is not None:
        adapter_plan_path = Path(materialization_path).parent / "live-step-plan.json"
    if (approval_path is None or materialization_path is None or executor is None
            or platform_source_root is None or application_source_root is None
            or adapter_plan_path is None):
        raise RunnerRejected("live-inputs-required")

    sealed = validate_live_gate(approval_path, materialization_path, profile_path,
                                platform_source_root, application_source_root,
                                adapter_plan_path, now=now)
    if getattr(executor, "plan_sha256", None) != sealed["liveAdapterPlanSha256"]:
        raise RunnerRejected("executor-plan-binding")
    profile_digest = _digest(Path(profile_path))
    materialization_digest = _digest(Path(materialization_path))
    target = sealed["profile"]["spec"]["target"]
    observed_identity = executor.identity(target)
    if (not isinstance(observed_identity, dict)
            or set(observed_identity) != {"context", "clusterUID"}
            or observed_identity["context"] != target["context"]):
        raise RunnerRejected("observed-identity")
    if observed_identity["clusterUID"] is not None and not isinstance(observed_identity["clusterUID"], str):
        raise RunnerRejected("observed-cluster-uid")
    journal_path = Path(journal_path)
    if journal_path.exists():
        journal = _load_json(journal_path)
        _validate_journal(journal, profile_digest, materialization_digest,
                          sealed["sourceSetSha256"], sealed["liveAdapterPlanSha256"],
                          sealed["liveAdapterInputSetSha256"],
                          sealed["inputDigests"], target, observed_identity)
    else:
        if mode != "first":
            raise RunnerRejected("journal-required")
        if observed_identity["clusterUID"] is not None:
            raise RunnerRejected("first-run-cluster-exists")
        journal = _new_journal(profile_digest, materialization_digest,
                               sealed["sourceSetSha256"], sealed["liveAdapterPlanSha256"],
                               sealed["liveAdapterInputSetSha256"],
                               sealed["inputDigests"], target, None)

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
            if observed.get("resourceDigest") != state["resourceDigest"]:
                raise RunnerRejected(f"completed-step-digest:{step}")
            continue
        must_reconcile = state["status"] in {"running", "uncertain"} or (
            step in NON_RECREATABLE_JOBS and state["attempts"] > 0)
        if must_reconcile:
            try:
                reconciled = executor.reconcile(step, state)
            except Exception as error:
                raise RunnerRejected(f"uncertain-step-reconcile:{step}") from error
            if not isinstance(reconciled, dict) or reconciled.get("state") not in {"succeeded", "incomplete"}:
                raise RunnerRejected(f"uncertain-step-evidence:{step}")
            if reconciled["state"] == "succeeded":
                digest_value = reconciled.get("resourceDigest")
                if not SHA256.fullmatch(digest_value or "") or not isinstance(reconciled.get("evidence"), dict):
                    raise RunnerRejected(f"uncertain-step-evidence:{step}")
                state.update(status="succeeded", evidence=reconciled["evidence"],
                             resourceDigest=digest_value, errorClass=None)
                if step == "cluster":
                    cluster_uid = reconciled["evidence"].get("clusterUID")
                    if not isinstance(cluster_uid, str) or not cluster_uid:
                        raise RunnerRejected("cluster-uid-evidence")
                    journal["target"]["clusterUID"] = cluster_uid
                journal_path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
                continue
            if step in NON_RECREATABLE_JOBS:
                raise RunnerRejected(f"uncertain-job-outcome:{step}")
            state.update(status="failed", errorClass="reconciled-incomplete")
        if mode == "rerun":
            raise RunnerRejected(f"rerun-incomplete-step:{step}")
        state["status"] = "running"
        state["attempts"] += 1
        journal_path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
        try:
            evidence = executor.run(step)
            if (not isinstance(evidence, dict) or evidence.get("succeeded") is not True
                    or not SHA256.fullmatch(evidence.get("resourceDigest") or "")):
                raise RunnerRejected(f"step-evidence:{step}")
        except Exception as error:
            state["status"] = "uncertain" if step in NON_RECREATABLE_JOBS else "failed"
            state["errorClass"] = type(error).__name__
            journal_path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
            raise
        state.update(status="succeeded", evidence=evidence,
                     resourceDigest=evidence["resourceDigest"], errorClass=None)
        if step == "cluster":
            cluster_uid = evidence.get("clusterUID")
            if not isinstance(cluster_uid, str) or not cluster_uid:
                raise RunnerRejected("cluster-uid-evidence")
            journal["target"]["clusterUID"] = cluster_uid
        journal_path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
    return journal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry-run", "first", "rerun", "resume"), required=True)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--materialization", type=Path)
    parser.add_argument("--profile", type=Path,
                        default=HERE / ".runtime" / "profile.runtime.local.yaml")
    parser.add_argument("--journal", type=Path, default=Path("fresh-profile-checkpoints.json"))
    parser.add_argument("--platform-source-root", type=Path)
    parser.add_argument("--application-source-root", type=Path)
    parser.add_argument("--live-plan", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "dry-run":
            result = execute(args.mode, args.approval, args.materialization, args.profile,
                             args.journal, None)
        else:
            if (args.live_plan is None or args.platform_source_root is None
                    or args.application_source_root is None):
                raise RunnerRejected("live-cli-inputs-required")
            adapter_module = _load_contract_module("approved_live_adapter")
            adapter = adapter_module.ApprovedLiveAdapter(args.live_plan)
            result = execute(args.mode, args.approval, args.materialization, args.profile,
                             args.journal, adapter, args.platform_source_root,
                             args.application_source_root, args.live_plan)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("runner rejected: " + str(error))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

