#!/usr/bin/env python3
"""Approved, target-bound CLI adapter for the fresh-profile runner.

This module does not run during materialization or dry-run.  In a separately
approved live invocation it executes only argv arrays from the approval-bound
plan, never a shell string.  Every command is pinned to an executable hash and
to the fresh kubeconfig/context or kind cluster name.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Protocol

ALLOWED_TOOLS = {"kubectl", "kind", "helm", "istioctl", "argocd", "rollouts"}
STEPS = (
    "preflight", "image-source", "cluster", "empty-node-cache", "image-import",
    "crds", "controllers", "security-boundary", "credential-bootstrap",
    "custom-resources", "database", "role-bootstrap", "migration", "application",
    "acceptance",
)
SENSITIVE_FRAGMENTS = ("--password", "--token", "--auth-token", "password=", "token=",
                       "-----begin ", "secretvalue")
REQUIRED_STEP_HEALTH = {
    "empty-node-cache": {"node-cache-empty"},
    "image-import": {"node-images-present"},
    "database": {"argocd-operation-succeeded", "statefulset-ready", "pvc-bound"},
    "role-bootstrap": {"argocd-operation-succeeded", "job-complete"},
    "migration": {"argocd-operation-succeeded", "job-complete"},
    "application": {"argocd-application-ready", "rollout-ready"},
}
STEP_RESOURCE_CONTRACTS = {
    "database": {
        ("argoproj.io/v1alpha1", "Application/fresh-control/fresh-account-release",
         "argocd-operation-succeeded"),
        ("v1", "PersistentVolumeClaim/fresh-account/account-data", "pvc-bound"),
        ("v1", "Service/fresh-account/account-postgresql", "none"),
        ("apps/v1", "StatefulSet/fresh-account/account-postgresql", "statefulset-ready"),
    },
    "role-bootstrap": {
        ("argoproj.io/v1alpha1", "Application/fresh-control/fresh-account-release",
         "argocd-operation-succeeded"),
        ("batch/v1", "Job/fresh-account/account-db-role-bootstrap", "job-complete"),
    },
    "migration": {
        ("argoproj.io/v1alpha1", "Application/fresh-control/fresh-account-release",
         "argocd-operation-succeeded"),
        ("batch/v1", "Job/fresh-account/account-migration", "job-complete"),
    },
    "application": {
        ("argoproj.io/v1alpha1", "Application/fresh-control/fresh-account-release",
         "argocd-application-ready"),
        ("argoproj.io/v1alpha1", "Rollout/fresh-account/account-api", "rollout-ready"),
        ("v1", "Service/fresh-account/account-api", "none"),
        ("networking.istio.io/v1beta1", "DestinationRule/fresh-account/account-api", "none"),
        ("networking.istio.io/v1beta1", "VirtualService/fresh-account/account-api", "none"),
    },
}
STEP_PHASES = {
    "preflight": "pre-cluster", "image-source": "pre-cluster",
    "cluster": "cluster-create",
    **{step: "post-cluster" for step in STEPS[3:]},
}
GITOPS_STEPS = {"database", "role-bootstrap", "migration", "application"}
ARGO_SYNC_RESOURCES = {
    "database": [
        ":PersistentVolumeClaim:account-data", ":Service:account-postgresql",
        "apps:StatefulSet:account-postgresql",
    ],
    "role-bootstrap": ["batch:Job:account-db-role-bootstrap"],
    "migration": ["batch:Job:account-migration"],
    "application": [
        "argoproj.io:Rollout:account-api", ":Service:account-api",
        "networking.istio.io:DestinationRule:account-api",
        "networking.istio.io:VirtualService:account-api",
    ],
}
KUBECTL_RESOURCES = {
    "application": ("argoproj.io/v1alpha1", "Application"),
    "configmap": ("v1", "ConfigMap"),
    "deployment": ("apps/v1", "Deployment"),
    "destinationrule": ("networking.istio.io/v1beta1", "DestinationRule"),
    "job": ("batch/v1", "Job"),
    "namespace": ("v1", "Namespace"),
    "node": ("v1", "Node"),
    "persistentvolumeclaim": ("v1", "PersistentVolumeClaim"),
    "pvc": ("v1", "PersistentVolumeClaim"),
    "rollout": ("argoproj.io/v1alpha1", "Rollout"),
    "service": ("v1", "Service"),
    "serviceaccount": ("v1", "ServiceAccount"),
    "statefulset": ("apps/v1", "StatefulSet"),
    "virtualservice": ("networking.istio.io/v1beta1", "VirtualService"),
}
HELM_RELEASES = {
    "crds": {("argo-rollouts-crds", "fresh-control")},
    "controllers": {
        ("argo-cd", "fresh-control"),
        ("argo-rollouts", "fresh-control"),
        ("istiod", "istio-system"),
    },
}


class AdapterRejected(ValueError):
    pass


class Transport(Protocol):
    def run(self, argv: list[str], env: dict[str, str]): ...


class SubprocessTransport:
    """The live transport.  It deliberately uses ``shell=False``."""

    def run(self, argv: list[str], env: dict[str, str]):
        return subprocess.run(argv, shell=False, capture_output=True, text=True,
                              check=False, env=env)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_sha256(path: Path) -> str:
    if path.is_file() and not path.is_symlink():
        return _sha256(path)
    if path.is_dir() and not path.is_symlink():
        files = sorted(item for item in path.rglob("*") if item.is_file())
        if any(item.is_symlink() for item in files):
            raise AdapterRejected("command-content-symlink")
        manifest = {item.relative_to(path).as_posix(): _sha256(item) for item in files}
        return _mapping_digest(manifest)
    raise AdapterRejected("command-content-path")


def _command_digest(results) -> str:
    payload = [{"returncode": item.returncode, "stdout": item.stdout, "stderr": item.stderr}
               for item in results]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _mapping_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def plan_input_manifest(plan: dict) -> dict:
    """Return every host byte sequence on which a live mutation may depend."""
    inputs = {}
    for step, actions in plan["steps"].items():
        for artifact in actions.get("hostArtifacts", []):
            key = f"host:{step}:{artifact['id']}:{artifact['path']}"
            inputs[key] = {
                "sha256": artifact["sha256"], "sizeBytes": artifact["sizeBytes"],
                "sourceIdentity": artifact["sourceIdentity"],
            }
        for section in ("verify", "reconcile", "run"):
            for index, command in enumerate(actions.get(section, [])):
                if command.get("contentSha256") is not None:
                    key = f"command:{step}:{section}:{index}:{command['tool']}"
                    inputs[key] = {"sha256": command["contentSha256"],
                                   "path": _bound_content_path(command)}
    return inputs


def plan_input_set_digest(plan: dict) -> str:
    return _mapping_digest(plan_input_manifest(plan))


def supply_plan_contract(supply_lock: dict, profile_spec: dict) -> dict:
    """Translate a validated G2 lock into the only plan identities it permits."""
    profile_images = {item["name"]: item["ref"] for item in profile_spec["images"]}
    if "kind-node" not in profile_images:
        raise AdapterRejected("supply-kind-node-missing")
    artifacts = {}
    for item in supply_lock["spec"]["images"]:
        source = item["source"]
        if source["kind"] != "retained-oci-archive":
            continue
        manifest = source["manifest"]
        selected_digest = manifest.get("childDigest", manifest.get("manifestDigest"))
        archive_digest = source.get("archiveSha256")
        if (not isinstance(archive_digest, str) or not archive_digest.startswith("sha256:")
                or not isinstance(selected_digest, str)):
            raise AdapterRejected("supply-archive-identity:" + item["name"])
        artifact_id = item["name"] + "-archive"
        source_identity = ":".join((
            "image-supply", item["name"], source["kind"], source["location"],
            archive_digest, selected_digest,
        ))
        artifacts[artifact_id] = {
            "sha256": archive_digest.removeprefix("sha256:"),
            "sourceIdentity": source_identity,
        }
    return {"kindNodeRef": profile_images["kind-node"], "hostArtifacts": artifacts}


def _bound_content_path(command: dict) -> str:
    args = command["args"]
    tool = command["tool"]
    if tool == "kubectl" and "--filename" in args:
        return args[args.index("--filename") + 1]
    if tool == "istioctl" and "--filename" in args:
        return args[args.index("--filename") + 1]
    if tool == "kind" and args[:2] == ["load", "image-archive"]:
        return args[2]
    if tool == "helm" and "--install" in args:
        return args[args.index("--install") + 2]
    raise AdapterRejected("command-content-unbound")


def _json_pointer(value, pointer: str):
    if not pointer.startswith("/"):
        raise AdapterRejected("observation-pointer")
    current = value
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not key.isdigit() or int(key) >= len(current):
                raise AdapterRejected("observation-pointer")
            current = current[int(key)]
        elif isinstance(current, dict) and key in current:
            current = current[key]
        else:
            raise AdapterRejected("observation-pointer")
    return current


class ApprovedLiveAdapter:
    """Concrete runner adapter backed by a sealed command plan.

    ``identity``, ``verify`` and ``reconcile`` are read-only plan sections.
    Only ``run`` uses the mutation section.  Exit code 3 in a reconcile probe
    means observable incomplete state; all other non-zero exits fail closed.
    """

    def __init__(self, plan_path: Path, transport: Transport | None = None,
                 expected_target: dict | None = None,
                 expected_versions: dict | None = None,
                 expected_image_digests: set[str] | None = None,
                 expected_supply_contract: dict | None = None):
        self.plan_path = Path(plan_path)
        self.plan_sha256 = _sha256(self.plan_path)
        self.plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.transport = transport or SubprocessTransport()
        self._validate_plan(expected_target, expected_versions, expected_image_digests,
                            expected_supply_contract)
        self.command_env = self._command_environment()

    def _validate_plan(self, expected_target, expected_versions, expected_image_digests,
                       expected_supply_contract):
        if set(self.plan) != {"schemaVersion", "target", "tools", "identity", "steps"}:
            raise AdapterRejected("plan-fields")
        if self.plan["schemaVersion"] != "platform.lab/approved-live-step-plan/v3":
            raise AdapterRejected("plan-version")
        target = self.plan["target"]
        if set(target) != {"clusterName", "context", "kubeconfig"}:
            raise AdapterRejected("target-fields")
        if (not target["clusterName"].startswith("learning-fresh-")
                or not target["context"].startswith("kind-learning-fresh-")
                or not Path(target["kubeconfig"]).is_absolute()):
            raise AdapterRejected("target-boundary")
        if expected_target is not None and any(
                target.get(name) != expected_target.get(name)
                for name in ("clusterName", "context")):
            raise AdapterRejected("target-contract")
        self.expected_kind_node_ref = (expected_supply_contract or {}).get("kindNodeRef")
        self._reject_environment_override(target)
        if set(self.plan["tools"]) != ALLOWED_TOOLS:
            raise AdapterRejected("tool-set")
        for alias, item in self.plan["tools"].items():
            if set(item) != {"path", "sha256", "version"}:
                raise AdapterRejected("tool-fields:" + alias)
            path = Path(item["path"])
            if not path.is_absolute() or not path.is_file() or _sha256(path) != item["sha256"]:
                raise AdapterRejected("tool-identity:" + alias)
            if (not isinstance(item["version"], str) or not item["version"]
                    or (expected_versions is not None
                        and item["version"] != expected_versions.get(alias))):
                raise AdapterRejected("tool-version:" + alias)
        if set(self.plan["identity"]) != {"clusterUID"}:
            raise AdapterRejected("identity-fields")
        if list(self.plan["steps"]) != list(STEPS):
            raise AdapterRejected("step-set")
        for command in self.plan["identity"].values():
            self._validate_command(command, section="identity", step=None,
                                   resource_identities=[])
        for step, actions in self.plan["steps"].items():
            if set(actions) != {"phase", "verify", "reconcile", "run", "resourceIdentities",
                                "observations", "hostArtifacts"}:
                raise AdapterRejected("step-fields:" + step)
            if actions["phase"] != STEP_PHASES[step]:
                raise AdapterRejected("step-phase:" + step)
            if not all(isinstance(actions[name], list)
                       for name in ("verify", "reconcile", "run", "hostArtifacts")):
                raise AdapterRejected("step-actions:" + step)
            if (not isinstance(actions["resourceIdentities"], list)
                    or not actions["resourceIdentities"]
                    or not all(isinstance(item, str) and item
                               for item in actions["resourceIdentities"])):
                raise AdapterRejected("step-resources:" + step)
            observations = actions["observations"]
            if (not isinstance(observations, list)
                    or any(not isinstance(item, dict) for item in observations)):
                raise AdapterRejected("step-observations:" + step)
            if step == "preflight":
                self._validate_preflight_step(actions)
                continue
            if step == "image-source":
                self._validate_image_source_step(actions, expected_supply_contract)
                continue
            if not observations or actions["hostArtifacts"]:
                raise AdapterRejected("step-cluster-contract:" + step)
            if not all(actions[name] for name in ("verify", "reconcile", "run")):
                raise AdapterRejected("step-cluster-commands:" + step)
            observation_identities = []
            for item in observations:
                self._validate_observation(item, step)
                observation_identities.append(item["identity"])
            if (len(observation_identities) != len(set(observation_identities))
                    or observation_identities != actions["resourceIdentities"]):
                raise AdapterRejected("step-observation-identities:" + step)
            required_health = REQUIRED_STEP_HEALTH.get(step)
            if required_health and not any(item["healthPolicy"] in required_health
                                           for item in observations):
                raise AdapterRejected("step-semantic-health:" + step)
            required_resources = STEP_RESOURCE_CONTRACTS.get(step)
            if required_resources is not None:
                actual_resources = {
                    (item["apiVersion"], item["identity"], item["healthPolicy"])
                    for item in observations
                }
                if actual_resources != required_resources:
                    raise AdapterRejected("step-resource-contract:" + step)
            if step in {"empty-node-cache", "image-import"}:
                self._validate_node_image_step(step, actions, expected_image_digests)
            for name in ("verify", "reconcile"):
                for command in actions[name]:
                    self._validate_command(command, section=name, step=step,
                                           resource_identities=actions["resourceIdentities"])
            for command in actions["run"]:
                self._validate_command(command, section="run", step=step,
                                       resource_identities=actions["resourceIdentities"])

    def _validate_preflight_step(self, actions):
        if (actions["hostArtifacts"] or actions["observations"]
                or actions["resourceIdentities"] != ["HostState/None/target-cluster-absent"]):
            raise AdapterRejected("preflight-contract")
        for section in ("verify", "reconcile", "run"):
            if len(actions[section]) != 1:
                raise AdapterRejected("preflight-command-count")
            self._validate_command(actions[section][0], section=section, step="preflight",
                                   resource_identities=actions["resourceIdentities"])

    def _validate_image_source_step(self, actions, expected_supply_contract):
        if actions["observations"] or any(actions[name] for name in ("verify", "reconcile", "run")):
            raise AdapterRejected("image-source-command-contract")
        artifacts = actions["hostArtifacts"]
        if not artifacts or any(not isinstance(item, dict) for item in artifacts):
            raise AdapterRejected("image-source-artifacts")
        identities = []
        for artifact in artifacts:
            if set(artifact) != {"id", "path", "sha256", "sizeBytes", "sourceIdentity"}:
                raise AdapterRejected("host-artifact-fields")
            if (not isinstance(artifact["id"], str) or not artifact["id"]
                    or not isinstance(artifact["sourceIdentity"], str)
                    or not artifact["sourceIdentity"]):
                raise AdapterRejected("host-artifact-identity")
            path = Path(artifact["path"])
            if (not path.is_absolute() or not path.is_file()
                    or path.stat().st_size != artifact["sizeBytes"]
                    or _sha256(path) != artifact["sha256"]):
                raise AdapterRejected("host-artifact-content:" + artifact["id"])
            identities.append("HostArtifact/None/" + artifact["id"])
        if identities != actions["resourceIdentities"] or len(identities) != len(set(identities)):
            raise AdapterRejected("host-artifact-resource-set")
        if expected_supply_contract is not None:
            expected = expected_supply_contract.get("hostArtifacts")
            if not isinstance(expected, dict) or set(expected) != {
                    item["id"] for item in artifacts}:
                raise AdapterRejected("host-artifact-supply-set")
            for artifact in artifacts:
                contract = expected[artifact["id"]]
                if (artifact["sha256"] != contract.get("sha256")
                        or artifact["sourceIdentity"] != contract.get("sourceIdentity")):
                    raise AdapterRejected("host-artifact-supply-identity:" + artifact["id"])

    def _validate_node_image_step(self, step, actions, expected_image_digests):
        expected_identity = ("Node/None/" + self.plan["target"]["clusterName"]
                             + "-control-plane")
        if len(actions["observations"]) != 1:
            raise AdapterRejected("node-image-observation-count:" + step)
        observation = actions["observations"][0]
        expected_health = "node-cache-empty" if step == "empty-node-cache" \
            else "node-images-present"
        if (observation["apiVersion"] != "v1" or observation["identity"] != expected_identity
                or observation["healthPolicy"] != expected_health):
            raise AdapterRejected("node-image-contract:" + step)
        digests = observation["requiredImageDigests"]
        if not digests or (expected_image_digests is not None
                           and set(digests) != set(expected_image_digests)):
            raise AdapterRejected("node-image-digest-set:" + step)

    def _validate_command(self, command: dict, section: str, step: str | None,
                          resource_identities: list[str]):
        if (set(command) != {"tool", "args", "contentSha256"}
                or command["tool"] not in self.plan["tools"]):
            raise AdapterRejected("command-fields")
        if not isinstance(command["args"], list) or not all(isinstance(arg, str) for arg in command["args"]):
            raise AdapterRejected("command-args")
        combined = " ".join(command["args"]).lower()
        if any(fragment in combined for fragment in SENSITIVE_FRAGMENTS):
            raise AdapterRejected("secret-in-command")
        target = self.plan["target"]
        alias = command["tool"]
        args = command["args"]
        content_digest = command["contentSha256"]
        if content_digest is not None and (not isinstance(content_digest, str)
                or len(content_digest) != 64
                or any(character not in "0123456789abcdef" for character in content_digest)):
            raise AdapterRejected("command-content-digest")
        validators = {
            "kubectl": self._validate_kubectl_command,
            "kind": self._validate_kind_command,
            "helm": self._validate_helm_command,
            "istioctl": self._validate_istioctl_command,
            "argocd": self._validate_argocd_command,
            "rollouts": self._validate_rollouts_command,
        }
        validators[alias](args, section, step, resource_identities, target, content_digest)

    @staticmethod
    def _exact_prefix(args: list[str], expected: list[str]) -> list[str]:
        if args[:len(expected)] != expected:
            raise AdapterRejected("command-target-prefix")
        return args[len(expected):]

    def _validate_kubectl_command(self, args, section, step, resources, target, content_digest):
        rest = self._exact_prefix(args, ["--kubeconfig", target["kubeconfig"],
                                         "--context", target["context"]])
        if step == "empty-node-cache" and section == "run":
            expected = ["get", "node", target["clusterName"] + "-control-plane",
                        "--output", "json"]
            if rest != expected or content_digest is not None:
                raise AdapterRejected("kubectl-node-proof-command")
            return
        if section == "identity":
            if content_digest is not None:
                raise AdapterRejected("kubectl-identity-content")
            if rest != ["get", "namespace", "kube-system", "--output",
                        "jsonpath={.metadata.uid}"]:
                raise AdapterRejected("kubectl-identity-command")
            return
        if section in {"verify", "reconcile"}:
            if content_digest is not None:
                raise AdapterRejected("kubectl-read-content")
            if step in {"empty-node-cache", "image-import"}:
                expected = ["get", "node", target["clusterName"] + "-control-plane",
                            "--output", "json"]
                if rest != expected:
                    raise AdapterRejected("kubectl-node-read-command")
                return
            if (len(rest) != 7 or rest[0] != "get" or rest[3] != "--namespace"
                    or rest[5:] != ["--output", "json"]):
                raise AdapterRejected("kubectl-read-command")
            resource, name, namespace = rest[1], rest[2], rest[4]
            api_kind = KUBECTL_RESOURCES.get(resource)
            if api_kind is None:
                raise AdapterRejected("kubectl-read-resource")
            identity = f"{api_kind[1]}/{namespace}/{name}"
            expected_targets = {
                (item["apiVersion"], item["identity"])
                for item in self.plan["steps"][step]["observations"]
            }
            if (api_kind[0], identity) not in expected_targets:
                raise AdapterRejected("kubectl-read-target")
            return
        if section == "run":
            if step in GITOPS_STEPS:
                raise AdapterRejected("kubectl-gitops-bypass:" + step)
            if len(rest) != 3 or rest[:2] != ["apply", "--filename"]:
                raise AdapterRejected("kubectl-run-command")
            manifest_path = Path(rest[2])
            if (not manifest_path.is_absolute() or not manifest_path.is_file()
                    or content_digest is None or _sha256(manifest_path) != content_digest):
                raise AdapterRejected("kubectl-manifest-path")
            expected_targets = {
                (item["apiVersion"], item["identity"])
                for item in self.plan["steps"][step]["observations"]
            }
            if self._manifest_identities(
                    manifest_path, self.plan["steps"][step]["observations"]) != expected_targets:
                raise AdapterRejected("kubectl-manifest-targets")
            return
        raise AdapterRejected("kubectl-operation")

    @staticmethod
    def _manifest_identities(path: Path, observations: list[dict]) -> set[tuple[str, str]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise AdapterRejected("kubectl-manifest-json") from error
        items = value.get("items") if isinstance(value, dict) and value.get("kind") == "List" else [value]
        if not isinstance(items, list) or not items or any(not isinstance(item, dict) for item in items):
            raise AdapterRejected("kubectl-manifest-objects")
        identities = set()
        resources = {}
        for item in items:
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                raise AdapterRejected("kubectl-manifest-metadata")
            api_version, kind, name = item.get("apiVersion"), item.get("kind"), metadata.get("name")
            namespace = metadata.get("namespace") or "None"
            if not all(isinstance(value, str) and value
                       for value in (api_version, kind, name, namespace)):
                raise AdapterRejected("kubectl-manifest-identity")
            identity = f"{kind}/{namespace}/{name}"
            target = (api_version, identity)
            if target in identities:
                raise AdapterRejected("kubectl-manifest-duplicate")
            identities.add(target)
            resources[identity] = item
        expected = {(item["apiVersion"], item["identity"]) for item in observations}
        if identities != expected:
            return identities
        for contract in observations:
            resource = resources[contract["identity"]]
            desired = {pointer: _json_pointer(resource, pointer)
                       for pointer in contract["desiredPaths"]}
            if _mapping_digest(desired) != contract["desiredSha256"]:
                raise AdapterRejected("kubectl-manifest-desired-digest:" + contract["identity"])
        return identities

    def _validate_kind_command(self, args, section, step, resources, target, content_digest):
        if step == "preflight":
            if args != ["get", "clusters"] or content_digest is not None:
                raise AdapterRejected("kind-preflight-command")
            return
        if section == "run" and step == "cluster":
            expected = ["create", "cluster", "--name", target["clusterName"],
                        "--kubeconfig", target["kubeconfig"]]
            if (args[:len(expected)] != expected or len(args) != len(expected) + 2
                    or args[-2] != "--image" or "@sha256:" not in args[-1]
                    or content_digest is not None
                    or (self.expected_kind_node_ref is not None
                        and args[-1] != self.expected_kind_node_ref)):
                raise AdapterRejected("kind-create-command")
            return
        if section == "run" and step == "image-import":
            if (len(args) != 5 or args[:2] != ["load", "image-archive"]
                    or not Path(args[2]).is_absolute() or not Path(args[2]).is_file()
                    or content_digest is None or _sha256(Path(args[2])) != content_digest
                    or args[3:] != ["--name", target["clusterName"]]):
                raise AdapterRejected("kind-load-command")
            return
        if section in {"verify", "reconcile"} and step in {"cluster", "empty-node-cache"}:
            if args != ["get", "nodes", "--name", target["clusterName"]]:
                raise AdapterRejected("kind-read-command")
            if content_digest is not None:
                raise AdapterRejected("kind-read-content")
            return
        raise AdapterRejected("kind-operation")

    def _validate_helm_command(self, args, section, step, resources, target, content_digest):
        prefix = ["--kubeconfig", target["kubeconfig"], "--kube-context", target["context"]]
        rest = self._exact_prefix(args, prefix)
        if section != "run" or step not in HELM_RELEASES:
            raise AdapterRejected("helm-operation")
        if len(rest) != 12 or rest[:2] != ["upgrade", "--install"]:
            raise AdapterRejected("helm-command")
        release, chart = rest[2], rest[3]
        if (not Path(chart).is_absolute() or rest[4] != "--namespace"
                or (release, rest[5]) not in HELM_RELEASES[step]
                or rest[6] != "--version" or not rest[7]
                or rest[8:] != ["--atomic", "--wait", "--wait-for-jobs", "--create-namespace"]
                or content_digest is None or _content_sha256(Path(chart)) != content_digest):
            raise AdapterRejected("helm-command")

    def _validate_istioctl_command(self, args, section, step, resources, target, content_digest):
        prefix = ["--kubeconfig", target["kubeconfig"], "--context", target["context"]]
        rest = self._exact_prefix(args, prefix)
        if (section != "run" or step != "controllers" or len(rest) != 4
                or rest[:2] != ["install", "--filename"]
                or not Path(rest[2]).is_absolute() or not Path(rest[2]).is_file()
                or content_digest is None or _sha256(Path(rest[2])) != content_digest
                or rest[3] != "--skip-confirmation"):
            raise AdapterRejected("istioctl-command")

    def _validate_argocd_command(self, args, section, step, resources, target, content_digest):
        if content_digest is not None:
            raise AdapterRejected("argocd-content")
        rest = self._exact_prefix(args, ["--core", "--kube-context", target["context"]])
        if section in {"verify", "reconcile"}:
            if len(rest) != 5 or rest[:2] != ["app", "get"] or rest[3:] != ["--output", "json"]:
                raise AdapterRejected("argocd-read-command")
            target = ("argoproj.io/v1alpha1", f"Application/fresh-control/{rest[2]}")
            expected_targets = {(item["apiVersion"], item["identity"])
                                for item in self.plan["steps"][step]["observations"]}
            if target not in expected_targets:
                raise AdapterRejected("argocd-read-target")
            return
        if section == "run" and step in GITOPS_STEPS:
            expected = ["app", "sync", "fresh-account-release"]
            for resource in ARGO_SYNC_RESOURCES[step]:
                expected.extend(["--resource", resource])
            expected.append("--prune=false")
            if rest != expected:
                raise AdapterRejected("argocd-run-command")
            target = ("argoproj.io/v1alpha1",
                      "Application/fresh-control/fresh-account-release")
            expected_targets = {(item["apiVersion"], item["identity"])
                                for item in self.plan["steps"][step]["observations"]}
            if target not in expected_targets:
                raise AdapterRejected("argocd-run-target")
            return
        raise AdapterRejected("argocd-operation")

    def _validate_rollouts_command(self, args, section, step, resources, target, content_digest):
        if content_digest is not None:
            raise AdapterRejected("rollouts-content")
        rest = self._exact_prefix(args, ["--kubeconfig", target["kubeconfig"],
                                         "--context", target["context"]])
        exact_target = "Rollout/fresh-account/account-api"
        expected_targets = {(item["apiVersion"], item["identity"])
                            for item in self.plan["steps"][step]["observations"]}
        if ("argoproj.io/v1alpha1", exact_target) not in expected_targets:
            raise AdapterRejected("rollouts-target")
        if section in {"verify", "reconcile"}:
            if rest != ["get", "rollout", "account-api", "--namespace", "fresh-account",
                        "--output", "json"]:
                raise AdapterRejected("rollouts-read-command")
            return
        if section == "run" and step == "acceptance":
            if rest not in (["restart", "rollout", "account-api", "--namespace", "fresh-account"],
                            ["promote", "rollout", "account-api", "--namespace", "fresh-account"],
                            ["abort", "rollout", "account-api", "--namespace", "fresh-account"],
                            ["retry", "rollout", "account-api", "--namespace", "fresh-account"]):
                raise AdapterRejected("rollouts-run-command")
            return
        raise AdapterRejected("rollouts-operation")

    @staticmethod
    def _require_pair(args: list[str], flag: str, expected: str):
        if args.count(flag) != 1 or any(arg.startswith(flag + "=") for arg in args):
            raise AdapterRejected("target-flag:" + flag)
        index = args.index(flag)
        if index + 1 >= len(args) or args[index + 1] != expected:
            raise AdapterRejected("target-value:" + flag)

    @staticmethod
    def _reject_environment_override(target: dict):
        kubeconfig = os.environ.get("KUBECONFIG")
        if kubeconfig and Path(kubeconfig).resolve() != Path(target["kubeconfig"]).resolve():
            raise AdapterRejected("environment-target-override:KUBECONFIG")
        for name in ("KUBERNETES_MASTER", "KUBE_API_SERVER", "HELM_KUBECONTEXT",
                     "HELM_KUBEAPISERVER", "ARGOCD_SERVER", "ARGOCD_AUTH_TOKEN",
                     "ARGOCD_OPTS", "ARGOCD_CONFIG_DIR", "ARGOCD_SERVER_NAME"):
            if os.environ.get(name):
                raise AdapterRejected("environment-target-override:" + name)

    def _command_environment(self):
        allowed = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "SSL_CERT_FILE",
                   "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
        environment = {name: os.environ[name] for name in allowed if name in os.environ}
        environment["KUBECONFIG"] = self.plan["target"]["kubeconfig"]
        return environment

    @staticmethod
    def _validate_observation(observation: dict, step: str):
        expected = {"apiVersion", "identity", "desiredPaths", "desiredSha256", "generationPolicy",
                    "healthPolicy", "requiredConditions", "requiredImageDigests"}
        if set(observation) != expected:
            raise AdapterRejected("observation-fields:" + step)
        identity = observation["identity"]
        api_version = observation["apiVersion"]
        if not isinstance(api_version, str) or not api_version or "/" not in api_version and api_version != "v1":
            raise AdapterRejected("observation-api-version:" + step)
        if (not isinstance(identity, str) or len(identity.split("/")) != 3
                or any(not part for part in identity.split("/"))):
            raise AdapterRejected("observation-identity:" + step)
        paths = observation["desiredPaths"]
        if (not isinstance(paths, list) or not paths or len(paths) != len(set(paths))
                or any(not isinstance(path, str) or not path.startswith("/")
                       or path.startswith(("/status", "/metadata/uid", "/metadata/resourceVersion",
                                           "/metadata/creationTimestamp", "/metadata/managedFields"))
                       for path in paths)):
            raise AdapterRejected("observation-paths:" + step)
        if identity.startswith("Secret/") and any(path.startswith(("/data", "/stringData")) for path in paths):
            raise AdapterRejected("observation-secret-data:" + step)
        digest = observation["desiredSha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise AdapterRejected("observation-digest:" + step)
        if observation["generationPolicy"] not in {"none", "observed-current"}:
            raise AdapterRejected("observation-generation:" + step)
        if observation["healthPolicy"] not in {"none", "conditions", "workload-ready", "job-complete",
                                                 "pvc-bound", "statefulset-ready", "rollout-ready",
                                                 "argocd-operation-succeeded",
                                                 "argocd-application-ready", "node-cache-empty",
                                                 "node-images-present"}:
            raise AdapterRejected("observation-health:" + step)
        conditions = observation["requiredConditions"]
        if (not isinstance(conditions, dict)
                or any(not isinstance(key, str) or not key or value not in {"True", "False", "Unknown"}
                       for key, value in conditions.items())):
            raise AdapterRejected("observation-conditions:" + step)
        image_digests = observation["requiredImageDigests"]
        if (not isinstance(image_digests, list) or len(image_digests) != len(set(image_digests))
                or any(not isinstance(item, str) or not item.startswith("sha256:")
                       or len(item) != 71 for item in image_digests)):
            raise AdapterRejected("observation-image-digests:" + step)
        if observation["healthPolicy"] == "conditions" and not conditions:
            raise AdapterRejected("observation-conditions:" + step)
        if observation["healthPolicy"] == "workload-ready" and observation["generationPolicy"] != "observed-current":
            raise AdapterRejected("observation-workload-generation:" + step)
        kind = identity.split("/", 1)[0]
        if observation["healthPolicy"] == "workload-ready" and kind not in {
                "Deployment", "StatefulSet", "Rollout"}:
            raise AdapterRejected("observation-workload-kind:" + step)
        if observation["healthPolicy"] == "job-complete" and kind != "Job":
            raise AdapterRejected("observation-job-kind:" + step)
        if observation["healthPolicy"] == "pvc-bound" and kind != "PersistentVolumeClaim":
            raise AdapterRejected("observation-pvc-kind:" + step)
        if observation["healthPolicy"] == "statefulset-ready" and kind != "StatefulSet":
            raise AdapterRejected("observation-statefulset-kind:" + step)
        if observation["healthPolicy"] == "rollout-ready" and kind != "Rollout":
            raise AdapterRejected("observation-rollout-kind:" + step)
        if observation["healthPolicy"].startswith("argocd-") and kind != "Application":
            raise AdapterRejected("observation-argocd-kind:" + step)
        if observation["healthPolicy"] in {"node-cache-empty", "node-images-present"}:
            if kind != "Node" or not image_digests:
                raise AdapterRejected("observation-node-image-kind:" + step)
        elif image_digests:
            raise AdapterRejected("observation-unexpected-image-digests:" + step)

    def _argv(self, command):
        return [self.plan["tools"][command["tool"]]["path"], *command["args"]]

    def _run_commands(self, commands, incomplete_exit_codes=frozenset()):
        results = []
        for command in commands:
            self._verify_command_content(command)
            result = self.transport.run(self._argv(command), dict(self.command_env))
            results.append(result)
            if result.returncode in incomplete_exit_codes:
                return results, False
            if result.returncode != 0:
                raise AdapterRejected(f"command-exit:{result.returncode}")
        return results, True

    @staticmethod
    def _verify_command_content(command):
        expected = command.get("contentSha256")
        if expected is None:
            return
        path = Path(_bound_content_path(command))
        if not path.is_absolute() or _content_sha256(path) != expected:
            raise AdapterRejected("command-content-changed")

    def _host_artifact_evidence(self, step):
        normalized = []
        for artifact in self.plan["steps"][step]["hostArtifacts"]:
            path = Path(artifact["path"])
            if (not path.is_file() or path.stat().st_size != artifact["sizeBytes"]
                    or _sha256(path) != artifact["sha256"]):
                raise AdapterRejected("host-artifact-changed:" + artifact["id"])
            normalized.append({
                "identity": "HostArtifact/None/" + artifact["id"],
                "sha256": artifact["sha256"], "sizeBytes": artifact["sizeBytes"],
                "sourceIdentity": artifact["sourceIdentity"],
            })
        return normalized, _mapping_digest(normalized)

    def _preflight_evidence(self, command):
        results, _ = self._run_commands([command])
        clusters = {line.strip() for line in results[0].stdout.splitlines() if line.strip()}
        target = self.plan["target"]["clusterName"]
        if target in clusters:
            raise AdapterRejected("preflight-target-already-exists")
        normalized = [{"identity": "HostState/None/target-cluster-absent",
                       "targetCluster": target, "initialTargetAbsent": True}]
        return normalized, _mapping_digest(normalized)

    @staticmethod
    def _resource_identity(resource: dict) -> str:
        metadata = resource.get("metadata")
        if not isinstance(metadata, dict):
            raise AdapterRejected("observation-metadata")
        kind = resource.get("kind")
        namespace = metadata.get("namespace") or "None"
        name = metadata.get("name")
        if not all(isinstance(item, str) and item for item in (kind, namespace, name)):
            raise AdapterRejected("observation-identity")
        return f"{kind}/{namespace}/{name}"

    def _observe(self, results, observations):
        resources = []
        for result in results:
            try:
                value = json.loads(result.stdout)
            except (TypeError, json.JSONDecodeError) as error:
                raise AdapterRejected("observation-json") from error
            if not isinstance(value, dict):
                raise AdapterRejected("observation-object")
            if value.get("kind") == "List":
                items = value.get("items")
                if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                    raise AdapterRejected("observation-list")
                resources.extend(items)
            else:
                resources.append(value)
        by_identity = {}
        for resource in resources:
            identity = self._resource_identity(resource)
            if identity in by_identity:
                raise AdapterRejected("observation-duplicate")
            by_identity[identity] = resource
        expected = [item["identity"] for item in observations]
        if set(by_identity) != set(expected):
            raise AdapterRejected("observation-resource-set")

        normalized = []
        for contract in observations:
            identity = contract["identity"]
            resource = by_identity[identity]
            if resource.get("apiVersion") != contract["apiVersion"]:
                raise AdapterRejected("observation-api-version:" + identity)
            metadata = resource["metadata"]
            uid = metadata.get("uid")
            if not isinstance(uid, str) or not uid:
                raise AdapterRejected("observation-uid:" + identity)
            desired = {path: _json_pointer(resource, path) for path in contract["desiredPaths"]}
            desired_digest = _mapping_digest(desired)
            if desired_digest != contract["desiredSha256"]:
                raise AdapterRejected("observation-desired-digest:" + identity)
            generation = metadata.get("generation")
            observed_generation = resource.get("status", {}).get("observedGeneration")
            if contract["generationPolicy"] == "observed-current":
                if (not isinstance(generation, int) or generation < 1
                        or not isinstance(observed_generation, int)
                        or observed_generation < generation):
                    raise AdapterRejected("observation-stale-generation:" + identity)
            status = resource.get("status", {})
            if not isinstance(status, dict):
                raise AdapterRejected("observation-status:" + identity)
            conditions = status.get("conditions", [])
            if not isinstance(conditions, list):
                raise AdapterRejected("observation-conditions:" + identity)
            condition_map = {}
            for condition in conditions:
                if isinstance(condition, dict) and isinstance(condition.get("type"), str):
                    condition_map[condition["type"]] = condition.get("status")
            for condition_type, expected_status in contract["requiredConditions"].items():
                if condition_map.get(condition_type) != expected_status:
                    raise AdapterRejected("observation-condition:" + identity + ":" + condition_type)
            health = contract["healthPolicy"]
            if health == "workload-ready":
                replicas = resource.get("spec", {}).get("replicas")
                ready = status.get("readyReplicas")
                available = status.get("availableReplicas")
                if (not isinstance(replicas, int) or replicas < 1
                        or not isinstance(ready, int) or ready < replicas
                        or not isinstance(available, int) or available < replicas):
                    raise AdapterRejected("observation-not-ready:" + identity)
            elif health == "job-complete":
                succeeded = status.get("succeeded")
                if (resource.get("kind") != "Job" or not isinstance(succeeded, int) or succeeded < 1
                        or condition_map.get("Complete") != "True"
                        or not isinstance(status.get("completionTime"), str)):
                    raise AdapterRejected("observation-job-incomplete:" + identity)
            elif health == "pvc-bound":
                capacity = status.get("capacity", {}).get("storage")
                if (resource.get("kind") != "PersistentVolumeClaim"
                        or status.get("phase") != "Bound"
                        or not isinstance(resource.get("spec", {}).get("volumeName"), str)
                        or not resource["spec"]["volumeName"]
                        or not isinstance(capacity, str) or not capacity):
                    raise AdapterRejected("observation-pvc-unbound:" + identity)
            elif health == "statefulset-ready":
                replicas = resource.get("spec", {}).get("replicas")
                revisions = (status.get("currentRevision"), status.get("updateRevision"))
                if (resource.get("kind") != "StatefulSet"
                        or not isinstance(replicas, int) or replicas < 1
                        or any(status.get(field) != replicas for field in
                               ("replicas", "readyReplicas", "currentReplicas", "updatedReplicas"))
                        or not all(isinstance(value, str) and value for value in revisions)
                        or revisions[0] != revisions[1]):
                    raise AdapterRejected("observation-statefulset-unready:" + identity)
            elif health == "rollout-ready":
                replicas = resource.get("spec", {}).get("replicas")
                hashes = (status.get("currentPodHash"), status.get("stableRS"))
                if (resource.get("kind") != "Rollout"
                        or not isinstance(replicas, int) or replicas < 1
                        or any(status.get(field) != replicas for field in
                               ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas"))
                        or not all(isinstance(value, str) and value for value in hashes)
                        or hashes[0] != hashes[1]
                        or condition_map.get("Available") != "True"):
                    raise AdapterRejected("observation-rollout-unready:" + identity)
            elif health in {"argocd-operation-succeeded", "argocd-application-ready"}:
                operation = status.get("operationState")
                sync_result = operation.get("syncResult") if isinstance(operation, dict) else None
                revision = sync_result.get("revision") if isinstance(sync_result, dict) else None
                sources = resource.get("spec", {}).get("sources")
                source = resource.get("spec", {}).get("source")
                revisions = []
                if isinstance(sources, list):
                    revisions.extend(item.get("targetRevision") for item in sources
                                     if isinstance(item, dict))
                if isinstance(source, dict):
                    revisions.append(source.get("targetRevision"))
                if (not isinstance(operation, dict) or operation.get("phase") != "Succeeded"
                        or not isinstance(operation.get("finishedAt"), str)
                        or not operation["finishedAt"] or not isinstance(revision, str)
                        or revision not in revisions):
                    raise AdapterRejected("observation-argocd-operation:" + identity)
                if health == "argocd-application-ready" and (
                        status.get("sync", {}).get("status") != "Synced"
                        or status.get("health", {}).get("status") != "Healthy"):
                    raise AdapterRejected("observation-argocd-not-ready:" + identity)
                condition_map["OperationPhase"] = operation["phase"]
                condition_map["OperationRevision"] = revision
                if health == "argocd-application-ready":
                    condition_map["Sync"] = status["sync"]["status"]
                    condition_map["Health"] = status["health"]["status"]
            elif health in {"node-cache-empty", "node-images-present"}:
                images = status.get("images")
                if not isinstance(images, list):
                    raise AdapterRejected("observation-node-images:" + identity)
                observed_names = {
                    name for image in images if isinstance(image, dict)
                    for name in image.get("names", []) if isinstance(name, str)
                }
                present = {digest for digest in contract["requiredImageDigests"]
                           if any(digest in name for name in observed_names)}
                if health == "node-cache-empty" and present:
                    raise AdapterRejected("observation-node-cache-not-empty:" + identity)
                if health == "node-images-present" and present != set(
                        contract["requiredImageDigests"]):
                    raise AdapterRejected("observation-node-images-missing:" + identity)
                condition_map["RequiredImageDigestSet"] = _mapping_digest(
                    sorted(contract["requiredImageDigests"]))
                condition_map["ObservedRequiredImageCount"] = len(present)
            normalized.append({
                "apiVersion": contract["apiVersion"], "identity": identity, "uid": uid,
                "generation": generation,
                "observedGeneration": observed_generation, "desiredSha256": desired_digest,
                "conditions": {key: condition_map.get(key) for key in sorted(contract["requiredConditions"])},
                "healthPolicy": health,
                "semanticStatus": {key: condition_map[key] for key in
                                   ("OperationPhase", "OperationRevision", "Sync", "Health",
                                    "RequiredImageDigestSet", "ObservedRequiredImageCount")
                                   if key in condition_map},
            })
        return normalized, _mapping_digest(normalized)

    def identity(self, target):
        if target["clusterName"] != self.plan["target"]["clusterName"] or target["context"] != self.plan["target"]["context"]:
            raise AdapterRejected("identity-target")
        # kubectl uses exit 1 when the fresh kubeconfig/context does not exist
        # before the cluster step.  On resume, a missing/unreachable identity
        # becomes ``None`` and the journal's recorded UID rejects the run.
        uid_results, uid_exists = self._run_commands(
            [self.plan["identity"]["clusterUID"]], incomplete_exit_codes={1, 3})
        cluster_uid = uid_results[-1].stdout.strip() if uid_exists else None
        # Context is not read from global kubectl state. Every approved command
        # carries the target context explicitly, so no context switch is needed.
        return {"context": self.plan["target"]["context"],
                "clusterUID": cluster_uid or None}

    def verify(self, step, checkpoint):
        if step == "preflight":
            evidence = checkpoint.get("evidence") if isinstance(checkpoint, dict) else None
            observations = evidence.get("resourceObservations") if isinstance(evidence, dict) else None
            if (not isinstance(observations, list) or len(observations) != 1
                    or observations[0].get("initialTargetAbsent") is not True
                    or observations[0].get("targetCluster") != self.plan["target"]["clusterName"]):
                raise AdapterRejected("preflight-checkpoint")
            digest = _mapping_digest(observations)
            return {"verified": True, "resourceDigest": digest,
                    "resourceObservations": observations}
        if step == "image-source":
            observed, digest = self._host_artifact_evidence(step)
            return {"verified": True, "resourceDigest": digest,
                    "resourceObservations": observed}
        if step == "empty-node-cache":
            evidence = checkpoint.get("evidence") if isinstance(checkpoint, dict) else None
            observations = evidence.get("resourceObservations") if isinstance(evidence, dict) else None
            if (not isinstance(observations, list) or len(observations) != 1
                    or observations[0].get("healthPolicy") != "node-cache-empty"
                    or observations[0].get("semanticStatus", {}).get(
                        "ObservedRequiredImageCount") != 0):
                raise AdapterRejected("empty-node-cache-checkpoint")
            digest = _mapping_digest(observations)
            return {"verified": True, "resourceDigest": digest,
                    "resourceObservations": observations}
        results, _ = self._run_commands(self.plan["steps"][step]["verify"])
        observed, digest = self._observe(results, self.plan["steps"][step]["observations"])
        return {"verified": True, "resourceDigest": digest,
                "resourceObservations": observed}

    def reconcile(self, step, checkpoint):
        if step == "preflight":
            observed, digest = self._preflight_evidence(
                self.plan["steps"][step]["reconcile"][0])
            return {"state": "succeeded", "evidence": {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed}, "resourceDigest": digest}
        if step == "image-source":
            observed, digest = self._host_artifact_evidence(step)
            return {"state": "succeeded", "evidence": {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed}, "resourceDigest": digest}
        results, complete = self._run_commands(
            self.plan["steps"][step]["reconcile"], incomplete_exit_codes={3})
        if not complete:
            return {"state": "incomplete"}
        observed, digest = self._observe(results, self.plan["steps"][step]["observations"])
        evidence = {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed}
        if step == "cluster":
            evidence["clusterUID"] = self.identity(self.plan["target"])["clusterUID"]
        return {"state": "succeeded", "evidence": evidence,
                "resourceDigest": digest}

    def run(self, step):
        if step == "preflight":
            observed, digest = self._preflight_evidence(self.plan["steps"][step]["run"][0])
            return {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed, "resourceDigest": digest,
                    "mutationInputDigests": {}}
        if step == "image-source":
            observed, digest = self._host_artifact_evidence(step)
            return {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed, "resourceDigest": digest,
                    "mutationInputDigests": {item["identity"]: item["sha256"]
                                             for item in observed}}
        if step == "empty-node-cache":
            verified, _ = self._run_commands(self.plan["steps"][step]["run"])
            observed, digest = self._observe(
                verified, self.plan["steps"][step]["observations"])
            return {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed, "resourceDigest": digest,
                    "mutationInputDigests": {}}
        results, _ = self._run_commands(self.plan["steps"][step]["run"])
        # Hash normalized post-mutation verification, not mutation stdout.
        verified, _ = self._run_commands(self.plan["steps"][step]["verify"])
        observed, digest = self._observe(verified, self.plan["steps"][step]["observations"])
        evidence = {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed,
                    "resourceDigest": digest,
                    "mutationInputDigests": {
                        _bound_content_path(command): command["contentSha256"]
                        for command in self.plan["steps"][step]["run"]
                        if command["contentSha256"] is not None}}
        if step == "cluster":
            cluster_uid = self.identity(self.plan["target"])["clusterUID"]
            if not cluster_uid:
                raise AdapterRejected("cluster-uid-missing")
            evidence["clusterUID"] = cluster_uid
        return evidence


def validate_plan(plan_path: Path, expected_target: dict,
                  expected_versions: dict,
                  expected_image_digests: set[str] | None = None,
                  expected_supply_contract: dict | None = None) -> dict:
    """Validate a plan and its tool bytes without executing any command."""
    return ApprovedLiveAdapter(plan_path, expected_target=expected_target,
                               expected_versions=expected_versions,
                               expected_image_digests=expected_image_digests,
                               expected_supply_contract=expected_supply_contract).plan

