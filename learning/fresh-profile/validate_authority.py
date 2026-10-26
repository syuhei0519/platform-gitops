#!/usr/bin/env python3
"""Validate PLAT-215's canonical offline authority and admission contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
MATRIX = HERE / "authority-matrix.json"
EVIDENCE = HERE / "fixtures" / "canonical-two-sha.json"
NEGATIVE = HERE / "fixtures" / "security-negative-fixtures.json"
CANONICAL = HERE / "security" / "canonical-contract.json"
RBAC = HERE / "security" / "rbac.yaml"
ADMISSION = HERE / "security" / "admission.yaml"
ACTORS = {"platform", "workload", "release-definition"}
DANGEROUS_VERBS = {"delete", "deletecollection", "bind", "escalate", "impersonate", "*"}
SHA = re.compile(r"[0-9a-f]{40}")
PLATFORM_TOKEN = "__PLATFORM_GITOPS_SHA__"
ARGO_CD_CRDS = ["applications.argoproj.io", "appprojects.argoproj.io"]
ROLLOUTS_CRDS = [
    "analysisruns.argoproj.io", "analysistemplates.argoproj.io",
    "clusteranalysistemplates.argoproj.io", "experiments.argoproj.io",
    "rollouts.argoproj.io",
]


class ContractError(ValueError):
    pass


def require(value, reason):
    if not value:
        raise ContractError(reason)


def load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_yaml(path: Path):
    return list(yaml.safe_load_all(Path(path).read_text(encoding="utf-8")))


def validate_canonical(contract):
    require(contract.get("schemaVersion") == "platform.lab/fresh-security-contract/v1", "canonical-version")
    require(contract.get("destinationServer") == "https://kubernetes.default.svc", "canonical-destination-server")
    runtime_image = contract.get("approvedRuntimeImage")
    require(runtime_image == "PENDING_PLAT211_VERIFIED_IMAGE" or re.fullmatch(
        r"ghcr\.io/syuhei0519/backend-app@sha256:[0-9a-f]{64}", runtime_image or ""),
        "canonical-runtime-image")
    namespaces = contract.get("namespaces", {})
    require(set(namespaces) == {"fresh-control", "fresh-account", "fresh-observability"}, "canonical-namespaces")
    for namespace, boundary in (("fresh-control", "control"), ("fresh-account", "account"),
                                ("fresh-observability", "observability")):
        require(namespaces[namespace].get("platform.lab/profile") == "account-lightweight-fresh",
                f"namespace-profile:{namespace}")
        require(namespaces[namespace].get("platform.lab/security-boundary") == boundary,
                f"namespace-boundary:{namespace}")
    require(namespaces["fresh-account"].get("istio.io/rev") == "fresh-1-31-2", "namespace-istio-revision")

    applications = contract.get("applications", {})
    require(set(applications) == {"account-lightweight-root", "fresh-release-definitions",
                                  "fresh-observability", "fresh-account-release"}, "canonical-applications")
    for name, application in applications.items():
        require(application["namespace"] == "fresh-control", f"application-namespace:{name}")
        require(application["repository"].endswith(".git"), f"application-repository:{name}")
        require(SHA.fullmatch(application["revision"]) or application["revision"] == PLATFORM_TOKEN,
                f"application-revision:{name}")
        require(application["destination"]["server"] == contract["destinationServer"],
                f"application-server:{name}")
        require(application["destination"]["namespace"] in namespaces, f"application-destination:{name}")
        require(application.get("project") in {"fresh-platform", "fresh-account"}, f"application-project:{name}")
    application_revision = applications["fresh-account-release"]["revision"]
    require(applications["account-lightweight-root"].get("sourceConfig") ==
            {"directory": {"recurse": True}}, "application-source-config:account-lightweight-root")
    require(applications["fresh-observability"].get("sourceConfig") ==
            {"directory": {"recurse": True}}, "application-source-config:fresh-observability")
    require(applications["fresh-release-definitions"].get("sourceConfig") == {"helm": {
        "releaseName": "fresh-account-release-definition", "parameters": [
            {"name": "workloadRevision", "value": application_revision},
            {"name": "accountRuntimeImage", "value": runtime_image},
        ]}}, "application-source-config:fresh-release-definitions")
    require(applications["fresh-account-release"].get("sourceConfig") == {"kustomize": {
        "images": ["ghcr.io/syuhei0519/backend-app=" + runtime_image]}},
        "application-source-config:fresh-account-release")

    controllers = contract.get("controllers", {})
    require(set(controllers) == {"StatefulSet/fresh-account/account-postgresql",
                                 "Job/fresh-account/account-db-role-bootstrap",
                                 "Job/fresh-account/account-migration",
                                 "Rollout/fresh-account/account-api"}, "canonical-controllers")
    secret_refs = {(name, key) for controller in controllers.values()
                   for name, keys in controller["allowedSecretKeys"].items() for key in keys}
    require(secret_refs == {("account-db-bootstrap", "admin-password"),
                            ("account-db-migration", "username"), ("account-db-migration", "password"),
                            ("account-db-runtime", "username"),
                            ("account-db-runtime", "password")},
            "canonical-secret-keys")
    require(contract.get("rbacRuleContract") == {
        "discoveryVerbs": ["get", "list", "watch"], "createVerbs": ["create"],
        "namedMutationVerbs": ["get", "update", "patch"], "deleteVerbs": [],
        "createIsBoundedByFailClosedAdmission": True}, "canonical-rbac-contract")
    require(contract.get("podCreationBoundary") == {
        "trustedCreateRequestor": "system:kube-controller-manager",
        "namespacedPodCreateSubjects": [], "ownerReferenceUidLookupInCel": False,
        "guarantee": "CREATE Pod requires trusted kube-controller-manager requestor plus canonical controller owner shape; namespaced GitOps and workload identities receive no Pod create verb",
        "liveEvidence": ["audit-create-username", "kubectl-auth-can-i-no-direct-pod",
                         "fake-owner-direct-pod-denied", "ephemeralcontainers-subresource-denied"]},
        "canonical-pod-create-boundary")
    require(contract.get("liveEvidence", {}).get("status") == "required-unrun", "canonical-live-boundary")
    return contract


def validate_matrix(matrix, canonical=None):
    canonical = validate_canonical(canonical or load(CANONICAL))
    require(matrix.get("version") == 1 and matrix.get("mode") == "offline-proposal", "matrix-version")
    require(matrix.get("canonicalContract") == "learning/fresh-profile/security/canonical-contract.json",
            "matrix-canonical-path")
    live = matrix.get("liveEvidence", {})
    require(live.get("required") is True and live.get("status") == "not-run", "live-evidence-boundary")
    require(set(matrix.get("sources", {})) == ACTORS, "source-actors")
    for actor, source in matrix["sources"].items():
        revision = source.get("commit", "")
        require(SHA.fullmatch(revision) or (actor == "platform" and revision == PLATFORM_TOKEN), f"source-sha:{actor}")
        require(source.get("repository", "").endswith(".git"), f"source-repository:{actor}")
        require(source.get("paths") and source.get("destinations") and source.get("kinds"), f"source-finite:{actor}")
        require(not any("*" in item for key in ("paths", "destinations", "kinds") for item in source[key]),
                f"source-wildcard:{actor}")
    for name, application in canonical["applications"].items():
        actors = [actor for actor, source in matrix["sources"].items()
                  if application["repository"] == source["repository"] and
                  application["revision"] == source["commit"] and application["path"] in source["paths"]]
        require(len(actors) == 1, f"canonical-source:{name}")

    identities = matrix.get("identities", {})
    require(identities.get("gitops-controller") ==
            "system:serviceaccount:fresh-control:argocd-application-controller", "gitops-identity")
    require(matrix.get("rbac", {}).get("workloadServiceAccountApiVerbs") == [], "workload-api-verbs")
    crd_operations = [item for item in matrix.get("bootstrapOperations", [])
                      if item.get("kind") == "CustomResourceDefinition"]
    require(crd_operations == [
        {"actor": "bootstrap-operator", "kind": "CustomResourceDefinition",
         "source": "argo-cd", "names": ARGO_CD_CRDS, "approval": "G1"},
        {"actor": "bootstrap-operator", "kind": "CustomResourceDefinition",
         "source": "argo-rollouts", "names": ROLLOUTS_CRDS, "approval": "G1"},
    ], "bootstrap-crd-operations")
    require(matrix.get("admission", {}).get("policies") ==
            ["fresh-application-boundary", "fresh-managed-resource-boundary",
             "fresh-account-pod-boundary"], "admission-policies")

    permissions = matrix.get("permissions", [])
    require(permissions, "permissions-empty")
    for permission in permissions:
        actor = permission.get("actor")
        require(actor in ACTORS and permission.get("scope") == "namespace", "permission-scope")
        require(permission.get("resource") and permission.get("resource") != "*", "permission-resource")
        verbs = set(permission.get("verbs", []))
        names = permission.get("resourceNames", [])
        require(verbs and not verbs.intersection(DANGEROUS_VERBS), "permission-verb")
        require(names and not any(name == "*" for name in names), "permission-resource-names")
        require(permission.get("namespace") in matrix["sources"][actor]["destinations"], "permission-destination")
        key = f"{permission.get('apiGroup', '')}/{permission['resource']}"
        canonical_names = canonical["resourceNames"].get(permission["namespace"], {}).get(key, [])
        bootstrap_names = canonical.get("bootstrapResourceNames", {}).get(permission["namespace"], {}).get(key, [])
        require(set(names).issubset(set(canonical_names) | set(bootstrap_names)),
                "permission-canonical-name")
    require(not any(item["actor"] == "workload" and item["scope"] == "cluster" for item in permissions),
            "workload-cluster-authority")

    boundary = matrix.get("podBoundary", {})
    require(boundary.get("namespace") == "fresh-account" and boundary.get("directPodsAllowed") is False,
            "pod-boundary")
    require(set(boundary.get("controllers", {})) == set(canonical["controllers"]), "pod-controller-set")
    require(boundary.get("enforcement") == "trusted-requestor-plus-owner-shape-plus-no-namespaced-pod-create"
            and boundary.get("trustedCreateRequestor") ==
            canonical["podCreationBoundary"]["trustedCreateRequestor"]
            and boundary.get("namespacedPodCreateSubjects") == []
            and boundary.get("ownerReferenceUidLookupInCel") is False
            and boundary.get("requiredLiveEvidence") == ["audit-create-username",
                "kubectl-auth-can-i-no-direct-pod", "fake-owner-direct-pod-denied",
                "ephemeralcontainers-subresource-denied"], "pod-requestor-boundary")
    for identity, expected in canonical["controllers"].items():
        actual = boundary["controllers"][identity]
        require(actual.get("serviceAccountName") == expected["serviceAccountName"] and
                actual.get("allowedSecretKeys") == expected["allowedSecretKeys"], f"pod-controller-contract:{identity}")
    return matrix


def matching_permission(matrix, operation):
    for permission in matrix["permissions"]:
        if any(permission.get(field) != operation.get(field) for field in ("actor", "scope", "apiGroup", "resource")):
            continue
        if permission.get("namespace") != operation.get("namespace"):
            continue
        if operation.get("verb") in permission["verbs"] and operation.get("name") in permission["resourceNames"]:
            return permission
    return None


def secret_key_refs(value, result):
    if isinstance(value, dict):
        ref = value.get("secretKeyRef")
        if isinstance(ref, dict):
            result.add((ref.get("name"), ref.get("key")))
        ref = value.get("secretRef")
        if isinstance(ref, dict):
            result.add((ref.get("name"), None))
        secret = value.get("secret")
        if isinstance(secret, dict):
            result.add((secret.get("secretName"), None))
        for child in value.values():
            secret_key_refs(child, result)
    elif isinstance(value, list):
        for child in value:
            secret_key_refs(child, result)


def validate_controller(canonical, controller, exact_secrets=True):
    identity = f"{controller.get('kind')}/{controller.get('namespace')}/{controller.get('name')}"
    expected = canonical["controllers"].get(identity)
    require(expected is not None, f"controller-identity:{identity}")
    pod = controller.get("podSpec", {})
    require(pod.get("serviceAccountName") == expected["serviceAccountName"], f"controller-service-account:{identity}")
    require(pod.get("automountServiceAccountToken") is False, f"controller-automount:{identity}")
    require(not pod.get("hostNetwork", False), f"controller-host-network:{identity}")
    for volume in pod.get("volumes", []):
        require("hostPath" not in volume and "secret" not in volume and
                not any("secret" in source for source in volume.get("projected", {}).get("sources", [])),
                f"controller-volume:{identity}")
    for container in pod.get("initContainers", []) + pod.get("containers", []):
        security = container.get("securityContext", {})
        require(security.get("privileged") is not True, f"controller-privileged:{identity}")
        require(security.get("allowPrivilegeEscalation") is not True, f"controller-escalation:{identity}")
    init_secret_refs = set()
    secret_key_refs(pod.get("initContainers", []), init_secret_refs)
    require(not init_secret_refs, f"controller-init-secret:{identity}")
    found = set()
    secret_key_refs(pod, found)
    allowed = {(name, key) for name, keys in expected["allowedSecretKeys"].items() for key in keys}
    require(found == allowed if exact_secrets else found.issubset(allowed), f"controller-secret-keys:{identity}")


def validate_rbac(canonical, documents):
    require(not any(item["kind"] in {"ClusterRole", "ClusterRoleBinding"} for item in documents), "cluster-rbac")
    roles = {item["metadata"]["namespace"]: item for item in documents if item["kind"] == "Role"}
    require(set(roles) == set(canonical["resourceNames"]), "rbac-role-namespaces")
    bindings = [item for item in documents if item["kind"] == "RoleBinding"]
    require({item["metadata"]["namespace"] for item in bindings} == set(roles), "rbac-binding-namespaces")
    for binding in bindings:
        require(binding["subjects"] == [{"kind": "ServiceAccount", "name": "argocd-application-controller",
                                         "namespace": "fresh-control"}], "rolebinding-subject")

    actual = set()
    for namespace, role in roles.items():
        for rule in role["rules"]:
            require(not set(rule["verbs"]).intersection(DANGEROUS_VERBS), "rbac-dangerous-verb")
            require(not set(rule["resources"]).intersection({"pods", "pods/ephemeralcontainers"}),
                    "rbac-pod-create")
            names = tuple(sorted(rule.get("resourceNames", [])))
            for group in rule["apiGroups"]:
                for resource in rule["resources"]:
                    for verb in rule["verbs"]:
                        actual.add((namespace, group, resource, verb, names))
    expected = set()
    for namespace, resources in canonical["resourceNames"].items():
        for compound, names in resources.items():
            group, resource = compound.split("/", 1)
            for verb in ("get", "list", "watch", "create"):
                expected.add((namespace, group, resource, verb, ()))
            for verb in ("get", "update", "patch"):
                expected.add((namespace, group, resource, verb, tuple(sorted(names))))
    require(actual == expected, "rbac-canonical-rules")


def validate_admission(canonical, documents):
    policies = {item["metadata"]["name"]: item for item in documents if item["kind"] == "ValidatingAdmissionPolicy"}
    bindings = {item["metadata"]["name"]: item for item in documents if item["kind"] == "ValidatingAdmissionPolicyBinding"}
    expected = {"fresh-application-boundary", "fresh-managed-resource-boundary", "fresh-account-pod-boundary"}
    require(set(policies) == expected and set(bindings) == expected, "admission-inventory")
    require(all(item["spec"].get("failurePolicy") == "Fail" for item in policies.values()), "admission-fail-open")
    app_expression = " ".join(item["expression"] for item in policies["fresh-application-boundary"]["spec"]["validations"])
    for name, app in canonical["applications"].items():
        for value in (name, app["project"], app["repository"], app["revision"], app["path"],
                      app["destination"]["server"], app["destination"]["namespace"]):
            require(value in app_expression, f"admission-application:{name}")
    for fragment in ("spec.sources", "workloadRevision", "accountRuntimeImage",
                     canonical["approvedRuntimeImage"],
                     "ghcr.io/syuhei0519/backend-app=" + canonical["approvedRuntimeImage"],
                     "kustomize.images", "fresh-account-release-definition",
                     "helm.valueFiles", "helm.fileParameters", "helm.values", "helm.valuesObject",
                     "helm.version", "helm.passCredentials", "helm.skipCrds",
                     "helm.ignoreMissingValueFiles", "helm.kubeVersion", "helm.apiVersions",
                     "helm.skipSchemaValidation", "kustomize.namePrefix", "kustomize.nameSuffix",
                     "kustomize.commonLabels", "kustomize.commonAnnotations",
                     "kustomize.forceCommonLabels", "kustomize.forceCommonAnnotations",
                     "kustomize.namespace", "kustomize.version", "kustomize.kubeVersion",
                     "kustomize.apiVersions", "kustomize.components",
                     "kustomize.ignoreMissingComponents", "kustomize.patches",
                     "kustomize.replicas", "kustomize.labelWithoutSelector"):
        require(fragment in app_expression, f"admission-source-fragment:{fragment}")
    managed_expression = " ".join(item["expression"] for item in policies["fresh-managed-resource-boundary"]["spec"]["validations"])
    for namespace, resources in canonical["resourceNames"].items():
        if namespace == "fresh-control":
            continue
        require(namespace in managed_expression, f"admission-namespace:{namespace}")
        for names in resources.values():
            require(all(name in managed_expression for name in names), f"admission-managed-name:{namespace}")
    pod_expression = " ".join(item["expression"] for item in policies["fresh-account-pod-boundary"]["spec"]["validations"])
    for fragment in ("ownerReferences", "initContainers", "ephemeralContainers", "request.subResource",
                     "request.userInfo.username", "system:kube-controller-manager", "secretRef", "secretKeyRef", "projected", "hostPath",
                     "account-db-bootstrap", "account-db-migration", "account-db-runtime"):
        require(fragment in pod_expression, f"admission-pod-fragment:{fragment}")
    require(bindings["fresh-application-boundary"]["spec"]["matchResources"]["namespaceSelector"] ==
            {"matchLabels": {"platform.lab/security-boundary": "control"}}, "application-binding-label")
    require(bindings["fresh-account-pod-boundary"]["spec"]["matchResources"]["namespaceSelector"] ==
            {"matchLabels": {"platform.lab/security-boundary": "account"}}, "pod-binding-label")


def validate_evidence(matrix, evidence, canonical=None):
    canonical = validate_canonical(canonical or load(CANONICAL))
    require(set(evidence.get("sources", {})) == ACTORS, "evidence-source-actors")
    for actor, actual in evidence["sources"].items():
        expected = matrix["sources"][actor]
        require(actual.get("repository") == expected["repository"], f"evidence-repository:{actor}")
        require(actual.get("commit") == expected["commit"], f"evidence-sha:{actor}")
        require(actual.get("path") in expected["paths"], f"evidence-path:{actor}")
    applications = {item["name"]: item for item in evidence.get("applications", [])}
    require(set(applications) == set(canonical["applications"]), "application-set")
    for name, expected in canonical["applications"].items():
        actual = applications[name]
        for actual_key, expected_key in (("namespace", "namespace"), ("project", "project"),
                                         ("repository", "repository"), ("revision", "revision"),
                                         ("path", "path"), ("sourceConfig", "sourceConfig"),
                                         ("destination", "destination")):
            require(actual.get(actual_key) == expected[expected_key], f"application-{actual_key}:{name}")
    require(evidence.get("operations") and all(matching_permission(matrix, item) for item in evidence["operations"]),
            "operation-authority")
    controllers = evidence.get("controllers", [])
    require({f"{item['kind']}/{item['namespace']}/{item['name']}" for item in controllers} ==
            set(canonical["controllers"]), "controller-set")
    for controller in controllers:
        validate_controller(canonical, controller)
    require(evidence.get("retained") == matrix.get("retainedSemantics"), "retained-semantics")
    return matrix, evidence


def negative_denied(canonical, obj, request=None):
    request = request or {"operation": "CREATE", "resource": "pods", "subResource": "",
                          "username": "system:kube-controller-manager"}
    kind = obj.get("kind")
    metadata = obj.get("metadata", {})
    if kind == "Application":
        expected = canonical["applications"].get(metadata.get("name"))
        if not expected:
            return True
        spec = obj.get("spec", {})
        source = spec.get("source", {})
        expected_source = {"repoURL": expected["repository"], "targetRevision": expected["revision"],
                           "path": expected["path"], **expected["sourceConfig"]}
        return not (metadata.get("namespace") == expected["namespace"] and spec.get("project") == expected["project"] and
                    source == expected_source and "sources" not in spec and
                    spec.get("destination") == expected["destination"])
    if kind == "Pod":
        if request.get("subResource") == "ephemeralcontainers":
            return True
        if request.get("operation") == "CREATE" and request.get("username") != \
                canonical["podCreationBoundary"]["trustedCreateRequestor"]:
            return True
        owners = metadata.get("ownerReferences", [])
        if not any(owner.get("controller") is True and
                   bool(owner.get("uid")) and
                   ((owner.get("kind") == "StatefulSet" and owner.get("name") == "account-postgresql") or
                    (owner.get("kind") == "Job" and owner.get("name") in {"account-db-role-bootstrap", "account-migration"}) or
                    (owner.get("kind") == "ReplicaSet" and owner.get("name", "").startswith("account-api-")))
                   for owner in owners):
            return True
        pod = obj.get("spec", {})
        if any("secret" in volume or "hostPath" in volume or
               any("secret" in source for source in volume.get("projected", {}).get("sources", []))
               for volume in pod.get("volumes", [])):
            return True
        return False
    if kind in {"StatefulSet", "Job", "Rollout"} and "podSpec" in obj:
        try:
            validate_controller(canonical, {"kind": kind, "namespace": metadata.get("namespace"),
                                             "name": metadata.get("name"), "podSpec": obj["podSpec"]})
            return False
        except ContractError:
            return True
    namespace = metadata.get("namespace")
    api_version = obj.get("apiVersion", "")
    group = api_version.split("/", 1)[0] if "/" in api_version else ""
    key = f"{group}/{kind.lower()}s"
    return metadata.get("name") not in canonical["resourceNames"].get(namespace, {}).get(key, [])


def validate_negatives(canonical, fixture):
    require(fixture.get("schemaVersion") == "platform.lab/security-negative-fixtures/v1", "negative-version")
    cases = fixture.get("cases", [])
    require({item["id"] for item in cases} == {"wrong-application-project", "multiple-application-sources",
                                                "release-helm-runtime-image-swap", "generated-child-image-swap",
                                                "release-helm-extra-values", "generated-child-kustomize-prefix",
                                                "direct-pod", "fake-owner-direct-pod", "secret-volume",
                                                "wrong-secret-key", "privileged-init-container",
                                                "secret-backed-init-container", "ephemeralcontainers-subresource",
                                                "runtime-uri-fallback", "migration-uri-fallback",
                                                "migration-job-bootstrap-secret",
                                                "arbitrary-managed-resource"},
            "negative-case-set")
    require(all(item.get("expected") == "deny" and
                negative_denied(canonical, item["object"], item.get("request")) for item in cases),
            "negative-not-denied")


def validate(matrix_path=MATRIX, evidence_path=EVIDENCE, canonical_path=CANONICAL,
             rbac_path=RBAC, admission_path=ADMISSION, negative_path=NEGATIVE):
    canonical = validate_canonical(load(canonical_path))
    matrix = validate_matrix(load(matrix_path), canonical)
    validate_rbac(canonical, load_yaml(rbac_path))
    validate_admission(canonical, load_yaml(admission_path))
    validate_negatives(canonical, load(negative_path))
    return validate_evidence(matrix, load(evidence_path), canonical)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--canonical", type=Path, default=CANONICAL)
    parser.add_argument("--rbac", type=Path, default=RBAC)
    parser.add_argument("--admission", type=Path, default=ADMISSION)
    parser.add_argument("--negative", type=Path, default=NEGATIVE)
    args = parser.parse_args()
    try:
        matrix, evidence = validate(args.matrix, args.evidence, args.canonical,
                                    args.rbac, args.admission, args.negative)
    except (ContractError, json.JSONDecodeError, yaml.YAMLError) as error:
        print(f"authority proposal rejected: {error}")
        return 1
    negative_count = len(load(args.negative).get("cases", []))
    print("authority proposal valid", f"permissions={len(matrix['permissions'])}",
          f"applications={len(evidence['applications'])}", f"negative={negative_count}")
    print("offline only; live can-i and admission observations remain required-unrun")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
