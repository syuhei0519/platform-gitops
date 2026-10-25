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
    "database": {"workload-ready", "conditions"},
    "role-bootstrap": {"job-complete"},
    "migration": {"job-complete"},
    "application": {"workload-ready", "conditions"},
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


def _command_digest(results) -> str:
    payload = [{"returncode": item.returncode, "stdout": item.stdout, "stderr": item.stderr}
               for item in results]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _mapping_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
                 expected_versions: dict | None = None):
        self.plan_path = Path(plan_path)
        self.plan_sha256 = _sha256(self.plan_path)
        self.plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.transport = transport or SubprocessTransport()
        self._validate_plan(expected_target, expected_versions)
        self.command_env = self._command_environment()

    def _validate_plan(self, expected_target, expected_versions):
        if set(self.plan) != {"schemaVersion", "target", "tools", "identity", "steps"}:
            raise AdapterRejected("plan-fields")
        if self.plan["schemaVersion"] != "platform.lab/approved-live-step-plan/v2":
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
            self._validate_command(command, read_only=True)
        for step, actions in self.plan["steps"].items():
            if set(actions) != {"verify", "reconcile", "run", "resourceIdentities", "observations"}:
                raise AdapterRejected("step-fields:" + step)
            if not all(isinstance(actions[name], list) and actions[name]
                       for name in ("verify", "reconcile", "run")):
                raise AdapterRejected("step-actions:" + step)
            if (not isinstance(actions["resourceIdentities"], list)
                    or not actions["resourceIdentities"]
                    or not all(isinstance(item, str) and item
                               for item in actions["resourceIdentities"])):
                raise AdapterRejected("step-resources:" + step)
            observations = actions["observations"]
            if (not isinstance(observations, list) or not observations
                    or any(not isinstance(item, dict) for item in observations)):
                raise AdapterRejected("step-observations:" + step)
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
            for name in ("verify", "reconcile"):
                for command in actions[name]:
                    self._validate_command(command, read_only=True)
            for command in actions["run"]:
                self._validate_command(command, read_only=False)

    def _validate_command(self, command: dict, read_only: bool):
        if set(command) != {"tool", "args"} or command["tool"] not in self.plan["tools"]:
            raise AdapterRejected("command-fields")
        if not isinstance(command["args"], list) or not all(isinstance(arg, str) for arg in command["args"]):
            raise AdapterRejected("command-args")
        combined = " ".join(command["args"]).lower()
        if any(fragment in combined for fragment in SENSITIVE_FRAGMENTS):
            raise AdapterRejected("secret-in-command")
        target = self.plan["target"]
        alias = command["tool"]
        args = command["args"]
        if alias in {"kubectl", "istioctl", "rollouts"}:
            self._require_pair(args, "--kubeconfig", target["kubeconfig"])
            self._require_pair(args, "--context", target["context"])
        elif alias == "helm":
            self._require_pair(args, "--kubeconfig", target["kubeconfig"])
            self._require_pair(args, "--kube-context", target["context"])
        elif alias == "kind":
            self._require_pair(args, "--name", target["clusterName"])
        elif alias == "argocd":
            if args.count("--core") != 1:
                raise AdapterRejected("argocd-core-required")
            self._require_pair(args, "--kube-context", target["context"])
            if any(flag in args or any(arg.startswith(flag + "=") for arg in args)
                   for flag in ("--server", "--config", "--auth-token")):
                raise AdapterRejected("argocd-target-override")
        if read_only and any(token in args for token in ("apply", "create", "delete", "patch", "replace",
                                                          "label", "annotate", "set", "sync", "rollout")):
            raise AdapterRejected("read-only-command")

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
        for name in ("HELM_KUBECONTEXT", "ARGOCD_SERVER", "ARGOCD_AUTH_TOKEN",
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
        expected = {"identity", "desiredPaths", "desiredSha256", "generationPolicy",
                    "healthPolicy", "requiredConditions"}
        if set(observation) != expected:
            raise AdapterRejected("observation-fields:" + step)
        identity = observation["identity"]
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
        if observation["healthPolicy"] not in {"none", "conditions", "workload-ready", "job-complete"}:
            raise AdapterRejected("observation-health:" + step)
        conditions = observation["requiredConditions"]
        if (not isinstance(conditions, dict)
                or any(not isinstance(key, str) or not key or value not in {"True", "False", "Unknown"}
                       for key, value in conditions.items())):
            raise AdapterRejected("observation-conditions:" + step)
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

    def _argv(self, command):
        return [self.plan["tools"][command["tool"]]["path"], *command["args"]]

    def _run_commands(self, commands, incomplete_exit_codes=frozenset()):
        results = []
        for command in commands:
            result = self.transport.run(self._argv(command), dict(self.command_env))
            results.append(result)
            if result.returncode in incomplete_exit_codes:
                return results, False
            if result.returncode != 0:
                raise AdapterRejected(f"command-exit:{result.returncode}")
        return results, True

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
            normalized.append({
                "identity": identity, "uid": uid, "generation": generation,
                "observedGeneration": observed_generation, "desiredSha256": desired_digest,
                "conditions": {key: condition_map.get(key) for key in sorted(contract["requiredConditions"])},
                "healthPolicy": health,
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
        results, _ = self._run_commands(self.plan["steps"][step]["verify"])
        observed, digest = self._observe(results, self.plan["steps"][step]["observations"])
        return {"verified": True, "resourceDigest": digest,
                "resourceObservations": observed}

    def reconcile(self, step, checkpoint):
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
        results, _ = self._run_commands(self.plan["steps"][step]["run"])
        # Hash normalized post-mutation verification, not mutation stdout.
        verified, _ = self._run_commands(self.plan["steps"][step]["verify"])
        observed, digest = self._observe(verified, self.plan["steps"][step]["observations"])
        evidence = {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed,
                    "resourceDigest": digest}
        if step == "cluster":
            cluster_uid = self.identity(self.plan["target"])["clusterUID"]
            if not cluster_uid:
                raise AdapterRejected("cluster-uid-missing")
            evidence["clusterUID"] = cluster_uid
        return evidence


def validate_plan(plan_path: Path, expected_target: dict,
                  expected_versions: dict) -> dict:
    """Validate a plan and its tool bytes without executing any command."""
    return ApprovedLiveAdapter(plan_path, expected_target=expected_target,
                               expected_versions=expected_versions).plan

