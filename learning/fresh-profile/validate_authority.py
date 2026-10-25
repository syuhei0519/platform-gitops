#!/usr/bin/env python3
"""Validate the offline PLAT-215 authority proposal; never contact Kubernetes."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml


HERE = Path(__file__).resolve().parent
MATRIX = HERE / "authority-matrix.json"
EVIDENCE = HERE / "fixtures" / "canonical-two-sha.json"
ACTORS = {"platform", "workload", "release-definition"}
DANGEROUS_VERBS = {"delete", "deletecollection", "bind", "escalate", "impersonate"}
SHA = re.compile(r"[0-9a-f]{40}")
PLATFORM_TOKEN = "__PLATFORM_GITOPS_SHA__"
REPOSITORY = HERE.parents[1]


class ContractError(ValueError):
    pass


def require(value, reason):
    if not value:
        raise ContractError(reason)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_matrix(matrix):
    require(matrix.get("version") == 1 and matrix.get("mode") == "offline-proposal", "matrix-version")
    live = matrix.get("liveEvidence", {})
    require(live.get("required") is True and live.get("status") == "not-run", "live-evidence-boundary")
    require(set(matrix.get("sources", {})) == ACTORS, "source-actors")
    for actor, source in matrix["sources"].items():
        revision = source.get("commit", "")
        require(SHA.fullmatch(revision) or (actor == "platform" and revision == PLATFORM_TOKEN),
                f"source-sha:{actor}")
        require(source.get("repository", "").endswith(".git"), f"source-repository:{actor}")
        require(source.get("paths") and source.get("destinations") and source.get("kinds"), f"source-finite:{actor}")
        require(not any("*" in item for key in ("paths", "destinations", "kinds") for item in source[key]),
                f"source-wildcard:{actor}")

    identities = matrix.get("identities", {})
    require(identities.get("gitops-controller") ==
            "system:serviceaccount:fresh-control:argocd-application-controller", "gitops-identity")
    require({identities.get(name) for name in ("database", "migration", "runtime")} == {
        "system:serviceaccount:fresh-account:account-database",
        "system:serviceaccount:fresh-account:account-migration",
        "system:serviceaccount:fresh-account:account-runtime"}, "workload-identities")
    require(matrix.get("rbac", {}).get("workloadServiceAccountApiVerbs") == [], "workload-api-verbs")
    require(matrix.get("admission", {}).get("policies") ==
            ["fresh-application-boundary", "fresh-account-pod-boundary"], "admission-policies")
    for field in ("rbac", "admission"):
        path = (REPOSITORY / matrix[field]["manifest"]).resolve()
        require(path.is_relative_to(REPOSITORY.resolve()) and path.is_file(), f"{field}-manifest")

    permissions = matrix.get("permissions", [])
    require(permissions, "permissions-empty")
    for permission in permissions:
        actor = permission.get("actor")
        require(actor in ACTORS, "permission-actor")
        require(permission.get("scope") in {"cluster", "namespace"}, "permission-scope")
        require(permission.get("resource") and permission.get("resource") != "*", "permission-resource")
        verbs = set(permission.get("verbs", []))
        names = permission.get("resourceNames", [])
        require(verbs and not verbs.intersection(DANGEROUS_VERBS | {"*"}), "permission-verb")
        require(names and not any(name == "*" for name in names), "permission-resource-names")
        if permission["scope"] == "cluster":
            require("namespace" not in permission, "cluster-namespace")
        else:
            require(permission.get("namespace") in matrix["sources"][actor]["destinations"],
                    "permission-destination")

    release_permissions = [item for item in permissions if item["actor"] == "release-definition"]
    require(release_permissions and all(item["apiGroup"] == "argoproj.io" and
                                        item["resource"] == "applications"
                                        for item in release_permissions), "release-definition-scope")
    require(not any(item["actor"] == "workload" and item["scope"] == "cluster" for item in permissions),
            "workload-cluster-authority")

    boundary = matrix.get("podBoundary", {})
    require(boundary.get("enforcement") == "trusted-namespace-plus-admission-required", "pod-enforcement")
    require(boundary.get("liveStatus") == "pending-separate-approval", "pod-live-status")
    require(boundary.get("directPodsAllowed") is False, "direct-pods")
    require(boundary.get("controllers"), "pod-controllers")
    return matrix


def matching_permission(matrix, operation):
    for permission in matrix["permissions"]:
        if any(permission.get(field) != operation.get(field)
               for field in ("actor", "scope", "apiGroup", "resource")):
            continue
        if permission["scope"] == "namespace" and permission.get("namespace") != operation.get("namespace"):
            continue
        if operation.get("verb") not in permission["verbs"]:
            continue
        if operation.get("name") not in permission["resourceNames"]:
            continue
        return permission
    return None


def secret_key_refs(value, result):
    if isinstance(value, dict):
        ref = value.get("secretKeyRef")
        if isinstance(ref, dict):
            result.add((ref.get("name"), ref.get("key")))
        secret = value.get("secret")
        if isinstance(secret, dict):
            result.add((secret.get("secretName"), None))
        for child in value.values():
            secret_key_refs(child, result)
    elif isinstance(value, list):
        for child in value:
            secret_key_refs(child, result)


def validate_controller(matrix, controller):
    boundary = matrix["podBoundary"]
    identity = f"{controller.get('kind')}/{controller.get('namespace')}/{controller.get('name')}"
    expected = boundary["controllers"].get(identity)
    require(expected is not None, f"controller-identity:{identity}")
    require(controller.get("namespace") == boundary["namespace"], f"controller-namespace:{identity}")
    pod = controller.get("podSpec", {})
    require(pod.get("serviceAccountName") == expected["serviceAccountName"], f"controller-service-account:{identity}")
    require(pod.get("automountServiceAccountToken") is False, f"controller-automount:{identity}")
    require(not pod.get("hostNetwork", False), f"controller-host-network:{identity}")
    for volume in pod.get("volumes", []):
        require("hostPath" not in volume and "projected" not in volume, f"controller-volume:{identity}")
    for container in pod.get("initContainers", []) + pod.get("containers", []):
        security = container.get("securityContext", {})
        require(security.get("privileged") is not True, f"controller-privileged:{identity}")
        require(security.get("allowPrivilegeEscalation") is not True, f"controller-escalation:{identity}")
    found = set()
    secret_key_refs(pod, found)
    allowed = {(name, key) for name, keys in expected["allowedSecretKeys"].items() for key in keys}
    require(found == allowed, f"controller-secret-keys:{identity}")


def validate_evidence(matrix, evidence):
    require(set(evidence.get("sources", {})) == ACTORS, "evidence-source-actors")
    for actor, actual in evidence["sources"].items():
        expected = matrix["sources"][actor]
        require(actual.get("repository") == expected["repository"], f"evidence-repository:{actor}")
        require(actual.get("commit") == expected["commit"], f"evidence-sha:{actor}")
        require(actual.get("path") in expected["paths"], f"evidence-path:{actor}")

    identities = set()
    for application in evidence.get("applications", []):
        actor = application.get("actor")
        source = matrix["sources"].get(actor, {})
        identity = f"{application.get('namespace')}/{application.get('name')}"
        require(identity not in identities, "application-duplicate")
        identities.add(identity)
        require(application.get("repository") == source.get("repository"), f"application-repository:{identity}")
        require(application.get("revision") == source.get("commit"), f"application-revision:{identity}")
        require(application.get("path") in source.get("paths", []), f"application-path:{identity}")
        require(application.get("namespace") in source.get("destinations", []), f"application-namespace:{identity}")
    require(identities == {"fresh-control/account-lightweight-root", "fresh-control/fresh-account-release"},
            "application-set")
    release = next(item for item in evidence["applications"] if item["actor"] == "release-definition")
    require(release["destination"] == "fresh-account", "release-destination")

    operations = evidence.get("operations", [])
    require(operations and all(matching_permission(matrix, operation) for operation in operations),
            "operation-authority")
    controllers = evidence.get("controllers", [])
    require({f"{item['kind']}/{item['namespace']}/{item['name']}" for item in controllers} ==
            set(matrix["podBoundary"]["controllers"]), "controller-set")
    for controller in controllers:
        validate_controller(matrix, controller)
    require(evidence.get("retained") == matrix.get("retainedSemantics"), "retained-semantics")
    return matrix, evidence


def validate(matrix_path: Path = MATRIX, evidence_path: Path = EVIDENCE):
    matrix = validate_matrix(load(matrix_path))
    return validate_evidence(matrix, load(evidence_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    args = parser.parse_args()
    try:
        matrix, evidence = validate(args.matrix, args.evidence)
    except (ContractError, json.JSONDecodeError) as error:
        print(f"authority proposal rejected: {error}")
        return 1
    print("authority proposal valid",
          f"actors={len(matrix['sources'])}",
          f"permissions={len(matrix['permissions'])}",
          f"controllers={len(evidence['controllers'])}")
    print("offline only; live can-i, admission denial and sync evidence remain not-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
