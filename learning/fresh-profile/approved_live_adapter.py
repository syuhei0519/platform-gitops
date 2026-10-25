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


class AdapterRejected(ValueError):
    pass


class Transport(Protocol):
    def run(self, argv: list[str]): ...


class SubprocessTransport:
    """The live transport.  It deliberately uses ``shell=False``."""

    def run(self, argv: list[str]):
        return subprocess.run(argv, shell=False, capture_output=True, text=True, check=False)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command_digest(results) -> str:
    payload = [{"returncode": item.returncode, "stdout": item.stdout, "stderr": item.stderr}
               for item in results]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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

    def _validate_plan(self, expected_target, expected_versions):
        if set(self.plan) != {"schemaVersion", "target", "tools", "identity", "steps"}:
            raise AdapterRejected("plan-fields")
        if self.plan["schemaVersion"] != "platform.lab/approved-live-step-plan/v1":
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
            if set(actions) != {"verify", "reconcile", "run", "resourceIdentities"}:
                raise AdapterRejected("step-fields:" + step)
            if not all(isinstance(actions[name], list) and actions[name]
                       for name in ("verify", "reconcile", "run")):
                raise AdapterRejected("step-actions:" + step)
            if (not isinstance(actions["resourceIdentities"], list)
                    or not actions["resourceIdentities"]
                    or not all(isinstance(item, str) and item
                               for item in actions["resourceIdentities"])):
                raise AdapterRejected("step-resources:" + step)
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
        elif alias == "argocd" and "--core" not in args:
            raise AdapterRejected("argocd-core-required")
        if read_only and any(token in args for token in ("apply", "create", "delete", "patch", "replace",
                                                          "label", "annotate", "set", "sync", "rollout")):
            raise AdapterRejected("read-only-command")

    @staticmethod
    def _require_pair(args: list[str], flag: str, expected: str):
        try:
            index = args.index(flag)
        except ValueError as error:
            raise AdapterRejected("target-flag:" + flag) from error
        if index + 1 >= len(args) or args[index + 1] != expected:
            raise AdapterRejected("target-value:" + flag)

    def _argv(self, command):
        return [self.plan["tools"][command["tool"]]["path"], *command["args"]]

    def _run_commands(self, commands, incomplete_exit_codes=frozenset()):
        results = []
        for command in commands:
            result = self.transport.run(self._argv(command))
            results.append(result)
            if result.returncode in incomplete_exit_codes:
                return results, False
            if result.returncode != 0:
                raise AdapterRejected(f"command-exit:{result.returncode}")
        return results, True

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
        return {"verified": True, "resourceDigest": _command_digest(results)}

    def reconcile(self, step, checkpoint):
        results, complete = self._run_commands(
            self.plan["steps"][step]["reconcile"], incomplete_exit_codes={3})
        if not complete:
            return {"state": "incomplete"}
        evidence = {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"]}
        if step == "cluster":
            evidence["clusterUID"] = self.identity(self.plan["target"])["clusterUID"]
        return {"state": "succeeded", "evidence": evidence,
                "resourceDigest": _command_digest(results)}

    def run(self, step):
        results, _ = self._run_commands(self.plan["steps"][step]["run"])
        # Hash normalized post-mutation verification, not mutation stdout.
        verified, _ = self._run_commands(self.plan["steps"][step]["verify"])
        evidence = {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceDigest": _command_digest(verified)}
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

