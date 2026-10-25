#!/usr/bin/env python3
"""Validate fresh root, observability, RBAC and admission design offline."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml


HERE = Path(__file__).resolve().parent
PROFILE = HERE / "profile.lock.yaml"
ROOT = HERE / "root" / "bootstrap.yaml"
CHILDREN = HERE / "root" / "children" / "applications.yaml"
OBSERVABILITY = HERE / "observability" / "resources.yaml"
RBAC = HERE / "security" / "rbac.yaml"
ADMISSION = HERE / "security" / "admission.yaml"
REVISION = re.compile(r"([0-9a-f]{40}|__PLATFORM_GITOPS_SHA__)")


class Rejected(ValueError):
    pass


def require(value, reason):
    if not value:
        raise Rejected(reason)


def load(path):
    return list(yaml.safe_load_all(Path(path).read_text(encoding="utf-8")))


def identity(item):
    metadata = item.get("metadata", {})
    return f"{item.get('kind')}/{metadata.get('namespace')}/{metadata.get('name')}"


def validate(root_path=ROOT, children_path=CHILDREN, observability_path=OBSERVABILITY,
             rbac_path=RBAC, admission_path=ADMISSION):
    profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))["spec"]
    root = load(root_path)
    require({identity(item) for item in root} == {
        "AppProject/fresh-control/fresh-platform",
        "AppProject/fresh-control/fresh-workload",
        "Application/fresh-control/account-lightweight-root",
    }, "root-inventory")
    root_app = next(item for item in root if item["kind"] == "Application")
    require(REVISION.fullmatch(root_app["spec"]["source"]["targetRevision"]), "root-revision")
    require(root_app["spec"]["source"]["path"] == "learning/fresh-profile/root/children", "root-path")

    children = load(children_path)
    require({identity(item) for item in children} == {
        "Application/fresh-control/fresh-release-definitions",
        "Application/fresh-control/fresh-observability",
    }, "child-inventory")
    releases = next(item for item in children if item["metadata"]["name"] == "fresh-release-definitions")
    require(releases["spec"]["source"]["targetRevision"] ==
            profile["repositories"]["application-manifest"]["commit"], "application-revision")
    observability_app = next(item for item in children if item["metadata"]["name"] == "fresh-observability")
    require(REVISION.fullmatch(observability_app["spec"]["source"]["targetRevision"]), "observability-revision")

    observability = load(observability_path)
    require(not any(item.get("kind") == "Secret" for item in observability), "observability-secret")
    require(not any("sonar" in identity(item).lower() for item in observability), "sonar-duplicated")
    expected_observability = {
        "ServiceAccount/fresh-observability/otel-collector",
        "ConfigMap/fresh-observability/otel-collector",
        "Deployment/fresh-observability/otel-collector",
        "Service/fresh-observability/otel-collector",
        "ServiceAccount/fresh-observability/prometheus",
        "StatefulSet/fresh-observability/prometheus",
        "ConfigMap/fresh-observability/prometheus",
        "Service/fresh-observability/prometheus",
        "ServiceAccount/fresh-observability/grafana",
        "Deployment/fresh-observability/grafana",
        "Service/fresh-observability/grafana",
    }
    require({identity(item) for item in observability} == expected_observability, "observability-inventory")
    profile_images = {item["name"]: item["ref"] for item in profile["images"]}
    images = {container["name"]: container["image"] for item in observability
              if item.get("kind") in {"Deployment", "StatefulSet"}
              for container in item["spec"]["template"]["spec"]["containers"]}
    require(images == {"collector": profile_images["otel-collector"],
                       "prometheus": profile_images["prometheus"],
                       "grafana": profile_images["grafana"]}, "observability-images")
    observed_hash = hashlib.sha256(Path(observability_path).read_bytes()).hexdigest()
    require(observed_hash == profile["budget"]["estimates"]["renderedRequestTotals"]
            ["platformObservabilityInputSha256"], "observability-measurement-input")

    rbac = load(rbac_path)
    require(not any(item["kind"] in {"ClusterRole", "ClusterRoleBinding"} for item in rbac), "cluster-rbac")
    roles = {identity(item) for item in rbac if item["kind"] == "Role"}
    require(roles == {
        "Role/fresh-control/argocd-fresh-control-writer",
        "Role/fresh-account/argocd-fresh-account-writer",
        "Role/fresh-observability/argocd-fresh-observability-writer",
    }, "role-inventory")
    for binding in (item for item in rbac if item["kind"] == "RoleBinding"):
        require(binding["subjects"] == [{"kind": "ServiceAccount", "name": "argocd-application-controller",
                                         "namespace": "fresh-control"}], "rolebinding-subject")
    require(not any("delete" in rule["verbs"] or "*" in rule["verbs"]
                    for role in rbac if role["kind"] == "Role" for rule in role["rules"]), "rbac-verb")

    admission = load(admission_path)
    require({identity(item) for item in admission} == {
        "ValidatingAdmissionPolicy/None/fresh-application-boundary",
        "ValidatingAdmissionPolicyBinding/None/fresh-application-boundary",
        "ValidatingAdmissionPolicy/None/fresh-account-pod-boundary",
        "ValidatingAdmissionPolicyBinding/None/fresh-account-pod-boundary",
    }, "admission-inventory")
    require(all(item.get("spec", {}).get("failurePolicy") == "Fail"
                for item in admission if item["kind"] == "ValidatingAdmissionPolicy"), "admission-fail-open")
    return root, children, observability, rbac, admission


if __name__ == "__main__":
    result = validate()
    print("fresh platform manifests valid",
          f"root={len(result[0])}", f"children={len(result[1])}",
          f"observability={len(result[2])}", f"rbac={len(result[3])}",
          f"admission={len(result[4])}")
