#!/usr/bin/env python3
"""Validate fresh root, observability, RBAC and admission design offline."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import yaml


HERE = Path(__file__).resolve().parent
PROFILE = HERE / "profile.template.yaml"
ROOT = HERE / "root" / "bootstrap.yaml"
CHILDREN = HERE / "root" / "children" / "applications.yaml"
OBSERVABILITY = HERE / "observability" / "resources.yaml"
RBAC = HERE / "security" / "rbac.yaml"
ADMISSION = HERE / "security" / "admission.yaml"
CANONICAL = HERE / "security" / "canonical-contract.json"
ARGOCD_VALUES = HERE.parents[1] / "bootstrap" / "argocd-values.yaml"
GRAFANA_SAFE_ENV = [
    {"name": "GF_SECURITY_DISABLE_INITIAL_ADMIN_CREATION", "value": "true"},
    {"name": "GF_AUTH_DISABLE_LOGIN", "value": "true"},
    {"name": "GF_AUTH_DISABLE_LOGIN_FORM", "value": "true"},
    {"name": "GF_AUTH_BASIC_ENABLED", "value": "false"},
    {"name": "GF_AUTH_ANONYMOUS_ENABLED", "value": "true"},
    {"name": "GF_AUTH_ANONYMOUS_ORG_ROLE", "value": "Viewer"},
    {"name": "GF_USERS_ALLOW_SIGN_UP", "value": "false"},
    {"name": "GF_USERS_ALLOW_ORG_CREATE", "value": "false"},
    {"name": "GF_USERS_VIEWERS_CAN_EDIT", "value": "false"},
    {"name": "GF_ANALYTICS_ENABLED", "value": "false"},
    {"name": "GF_ANALYTICS_REPORTING_ENABLED", "value": "false"},
    {"name": "GF_ANALYTICS_CHECK_FOR_UPDATES", "value": "false"},
    {"name": "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES", "value": "false"},
    {"name": "GF_SNAPSHOTS_EXTERNAL_ENABLED", "value": "false"},
    {"name": "GF_PLUGINS_PLUGIN_ADMIN_ENABLED", "value": "false"},
    {"name": "GF_PLUGINS_PREINSTALL_DISABLED", "value": "true"},
    {"name": "GF_PLUGINS_PREINSTALL_AUTO_UPDATE", "value": "false"},
]

AUTHORITY_SPEC = importlib.util.spec_from_file_location("fresh_authority", HERE / "validate_authority.py")
authority = importlib.util.module_from_spec(AUTHORITY_SPEC)
AUTHORITY_SPEC.loader.exec_module(authority)


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


def validate_application(application, expected):
    name = application["metadata"]["name"]
    spec = application["spec"]
    source = spec["source"]
    require(application["metadata"].get("namespace") == expected["namespace"], f"application-namespace:{name}")
    require(spec.get("project") == expected["project"], f"application-project:{name}")
    require(source.get("repoURL") == expected["repository"], f"application-repository:{name}")
    require(source.get("targetRevision") == expected["revision"], f"application-revision:{name}")
    require(source.get("path") == expected["path"], f"application-path:{name}")
    expected_source = {"repoURL": expected["repository"], "targetRevision": expected["revision"],
                       "path": expected["path"], **expected["sourceConfig"]}
    require(source == expected_source and "sources" not in spec, f"application-source-config:{name}")
    require(spec.get("destination") == expected["destination"], f"application-destination:{name}")


def validate_argocd_cache_scope(canonical, values_path=ARGOCD_VALUES):
    values = yaml.safe_load(Path(values_path).read_text(encoding="utf-8"))
    configs = values.get("configs", {})
    cm = configs.get("cm", {})
    require("resource.respectRBAC" not in cm, "argocd-cache-respect-rbac")
    require("resource.exclusions" not in cm, "argocd-cache-custom-exclusions")
    inclusions = yaml.safe_load(cm.get("resource.inclusions", ""))
    require(isinstance(inclusions, list), "argocd-cache-inclusions-shape")
    actual = {}
    for rule in inclusions:
        require(rule.get("clusters") == ["*"],
                "argocd-cache-inclusion-cluster")
        require(len(rule.get("apiGroups", [])) == 1, "argocd-cache-inclusion-group")
        group = rule["apiGroups"][0]
        require(group not in actual, "argocd-cache-inclusion-duplicate")
        actual[group] = rule.get("kinds", [])
    require(actual == canonical["argocdCacheScope"]["resourceInclusions"],
            "argocd-cache-inclusions")
    require("Secret" not in {kind for kinds in actual.values() for kind in kinds},
            "argocd-cache-secret-read")

    credentials = configs.get("clusterCredentials", {})
    require(set(credentials) == {canonical["argocdCacheScope"]["clusterName"]},
            "argocd-cluster-registration-inventory")
    cluster = credentials[canonical["argocdCacheScope"]["clusterName"]]
    require(set(cluster) == {"server", "namespaces", "clusterResources", "config"},
            "argocd-cluster-registration-fields")
    require(cluster.get("server") == canonical["argocdCacheScope"]["server"],
            "argocd-cluster-registration-server")
    require(cluster.get("namespaces") == ",".join(canonical["argocdCacheScope"]["namespaces"]),
            "argocd-cluster-registration-namespaces")
    require(cluster.get("clusterResources") == canonical["argocdCacheScope"]["clusterResources"],
            "argocd-cluster-registration-resources")
    require(cluster.get("config") == {"tlsClientConfig": {"insecure": False}},
            "argocd-cluster-registration-config")
    forbidden = {"username", "password", "bearerToken", "certData", "keyData", "caData",
                 "execProviderConfig", "awsAuthConfig"}
    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()
    require(not forbidden.intersection(keys(cluster.get("config"))),
            "argocd-cluster-registration-credential")
    return values


def validate(root_path=ROOT, children_path=CHILDREN, observability_path=OBSERVABILITY,
             rbac_path=RBAC, admission_path=ADMISSION, canonical_path=CANONICAL,
             argocd_values_path=ARGOCD_VALUES):
    profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))["spec"]
    canonical = authority.validate_canonical(authority.load(canonical_path))
    validate_argocd_cache_scope(canonical, argocd_values_path)
    root = load(root_path)
    require({identity(item) for item in root} == {
        "Namespace/None/fresh-control", "Namespace/None/fresh-account", "Namespace/None/fresh-observability",
        "AppProject/fresh-control/fresh-platform", "AppProject/fresh-control/fresh-account",
        "Application/fresh-control/account-lightweight-root", "Application/fresh-control/fresh-observability",
    }, "root-inventory")
    for namespace in (item for item in root if item["kind"] == "Namespace"):
        name = namespace["metadata"]["name"]
        require(namespace["metadata"].get("labels") == canonical["namespaces"][name], f"namespace-labels:{name}")

    projects = {item["metadata"]["name"]: item for item in root if item["kind"] == "AppProject"}
    require(projects["fresh-platform"]["spec"]["sourceRepos"] == [
        "https://github.com/syuhei0519/platform-gitops.git",
        "https://github.com/syuhei0519/application-manifest.git"], "platform-project-repositories")
    require(projects["fresh-account"]["spec"]["sourceRepos"] ==
            ["https://github.com/syuhei0519/application-manifest.git"], "account-project-repositories")
    require(projects["fresh-account"]["spec"]["destinations"] ==
            [{"server": canonical["destinationServer"], "namespace": "fresh-account"}],
            "account-project-destination")

    applications = {item["metadata"]["name"]: item for item in root if item["kind"] == "Application"}
    for name in ("account-lightweight-root", "fresh-observability"):
        validate_application(applications[name], canonical["applications"][name])

    children = load(children_path)
    require({identity(item) for item in children} ==
            {"Application/fresh-control/fresh-release-definitions"}, "child-inventory")
    release = children[0]
    validate_application(release, canonical["applications"]["fresh-release-definitions"])
    parameters = {item["name"]: item["value"] for item in release["spec"]["source"]["helm"]["parameters"]}
    require(parameters == {"workloadRevision": canonical["applications"]["fresh-account-release"]["revision"],
                           "accountRuntimeImage": canonical["approvedRuntimeImage"]},
            "release-helm-parameters")
    require(profile["repositories"]["application-manifest"]["commit"] ==
            canonical["applications"]["fresh-account-release"]["revision"], "profile-application-revision")

    observability = load(observability_path)
    require(not any(item.get("kind") == "Secret" for item in observability), "observability-secret")
    require(not any("sonar" in identity(item).lower() for item in observability), "sonar-duplicated")
    expected_observability = {
        "ServiceAccount/fresh-observability/otel-collector", "ConfigMap/fresh-observability/otel-collector",
        "Deployment/fresh-observability/otel-collector", "Service/fresh-observability/otel-collector",
        "ServiceAccount/fresh-observability/prometheus", "StatefulSet/fresh-observability/prometheus",
        "ConfigMap/fresh-observability/prometheus", "Service/fresh-observability/prometheus",
        "ServiceAccount/fresh-observability/grafana", "Deployment/fresh-observability/grafana",
        "Service/fresh-observability/grafana",
    }
    require({identity(item) for item in observability} == expected_observability, "observability-inventory")
    profile_images = {item["name"]: item["ref"] for item in profile["images"]}
    images = {container["name"]: container["image"] for item in observability
              if item.get("kind") in {"Deployment", "StatefulSet"}
              for container in item["spec"]["template"]["spec"]["containers"]}
    require(images == {"collector": profile_images["otel-collector"], "prometheus": profile_images["prometheus"],
                       "grafana": profile_images["grafana"]}, "observability-images")
    grafana = next(item for item in observability
                   if identity(item) == "Deployment/fresh-observability/grafana")
    require(grafana["spec"].get("replicas") == 1 and
            grafana["spec"].get("strategy") == {"type": "Recreate"},
            "grafana-recreate")
    grafana_container = next(container for container in
                             grafana["spec"]["template"]["spec"]["containers"]
                             if container.get("name") == "grafana")
    require(grafana_container.get("resources") == {
        "requests": {"cpu": "50m", "memory": "128Mi"},
        "limits": {"memory": "512Mi"},
    }, "grafana-resources")
    require(grafana_container.get("env") == GRAFANA_SAFE_ENV, "grafana-safe-env")
    require(grafana_container.get("readinessProbe") == {
        "httpGet": {"path": "/api/health", "port": "http"}
    }, "grafana-readiness")
    require("livenessProbe" not in grafana_container and
            "startupProbe" not in grafana_container, "grafana-no-auto-restart-probes")
    observed_hash = hashlib.sha256(Path(observability_path).read_bytes()).hexdigest()
    require(observed_hash == profile["budget"]["estimates"]["renderedRequestTotals"]
            ["platformObservabilityInputSha256"], "observability-measurement-input")

    try:
        authority.validate_rbac(canonical, load(rbac_path))
        authority.validate_admission(canonical, load(admission_path))
    except authority.ContractError as error:
        raise Rejected(str(error)) from error
    return root, children, observability, load(rbac_path), load(admission_path)


if __name__ == "__main__":
    result = validate()
    print("fresh platform manifests valid", f"root={len(result[0])}", f"children={len(result[1])}",
          f"observability={len(result[2])}", f"rbac={len(result[3])}", f"admission={len(result[4])}")
