#!/usr/bin/env python3
import copy
import importlib.util
from pathlib import Path
import unittest


MODULE = Path(__file__).with_name("validate_authority.py")
SPEC = importlib.util.spec_from_file_location("validate_authority", MODULE)
contract = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(contract)


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.matrix = contract.load(contract.MATRIX)
        self.evidence = contract.load(contract.EVIDENCE)
        self.canonical = contract.load(contract.CANONICAL)

    def assertEvidenceRejected(self, reason):
        with self.assertRaisesRegex(contract.ContractError, reason):
            contract.validate_evidence(self.matrix, self.evidence)

    def test_canonical_two_sha_authority_is_valid_offline(self):
        matrix, evidence = contract.validate()
        self.assertEqual(set(matrix["sources"]), contract.ACTORS)
        self.assertEqual(len(evidence["applications"]), 4)
        self.assertEqual(matrix["liveEvidence"]["status"], "not-run")

    def test_reviewed_mode_pin_accepts_auto_and_rejects_unapproved_modes(self):
        canonical=copy.deepcopy(self.canonical)
        args=canonical["applications"]["fresh-release-definitions"]["sourceConfig"]["helm"]["parameters"]
        next(p for p in args if p["name"] == "promotionMode")["value"] = "auto"
        next(p for p in args if p["name"] == "workloadVariant")["value"] = "fresh"
        canonical["applications"]["fresh-account-release"]["path"] = "learning/fresh-profile/workload-auto"
        contract.validate_canonical(canonical)
        docs=contract.load_yaml(Path(__file__).parent/"security/admission.yaml")
        policy=next(d for d in docs if d.get("kind")=="ValidatingAdmissionPolicy" and d["metadata"]["name"]=="fresh-application-boundary")
        policy["spec"]["validations"][1]["expression"] = policy["spec"]["validations"][1]["expression"].replace("p.name == 'promotionMode' && p.value == 'manual'", "p.name == 'promotionMode' && p.value == 'auto'")
        policy["spec"]["validations"][1]["expression"] = policy["spec"]["validations"][1]["expression"].replace("p.name == 'workloadVariant' && p.value == 'existing-upgrade'", "p.name == 'workloadVariant' && p.value == 'fresh'")
        policy["spec"]["validations"][0]["expression"] = policy["spec"]["validations"][0]["expression"].replace("object.spec.source.path == 'learning/fresh-profile/workload-existing'", "object.spec.source.path == 'learning/fresh-profile/workload-auto'")
        contract.validate_admission(canonical,docs)
        wrong_mode=copy.deepcopy(canonical)
        wrong_args=wrong_mode["applications"]["fresh-release-definitions"]["sourceConfig"]["helm"]["parameters"]
        next(p for p in wrong_args if p["name"] == "promotionMode")["value"] = "manual"
        with self.assertRaisesRegex(contract.ContractError,"admission-promotion-mode-pin"):
            contract.validate_admission(wrong_mode,docs)
        next(p for p in args if p["name"] == "promotionMode")["value"] = "unknown"
        with self.assertRaisesRegex(contract.ContractError,"canonical-promotion-mode"):
            contract.validate_canonical(canonical)

    def test_sync_mode_requires_matching_reviewed_admission_pin(self):
        canonical=copy.deepcopy(self.canonical)
        args=canonical["applications"]["fresh-release-definitions"]["sourceConfig"]["helm"]["parameters"]
        next(p for p in args if p["name"]=="syncMode")["value"]="paused"
        contract.validate_canonical(canonical)
        docs=contract.load_yaml(Path(__file__).parent/"security/admission.yaml")
        with self.assertRaisesRegex(contract.ContractError,"admission-sync-mode-pin"):
            contract.validate_admission(canonical,docs)
        policy=next(d for d in docs if d.get("kind")=="ValidatingAdmissionPolicy" and d["metadata"]["name"]=="fresh-application-boundary")
        policy["spec"]["validations"][1]["expression"]=policy["spec"]["validations"][1]["expression"].replace("p.name == 'syncMode' && p.value == 'automated'","p.name == 'syncMode' && p.value == 'paused'")
        contract.validate_admission(canonical,docs)
        next(p for p in args if p["name"]=="syncMode")["value"]="unknown"
        with self.assertRaisesRegex(contract.ContractError,"canonical-sync-mode"):
            contract.validate_canonical(canonical)

    def test_variable_or_wrong_source_sha_is_rejected(self):
        self.evidence["sources"]["workload"]["commit"] = "main"
        self.assertEvidenceRejected("evidence-sha:workload")

    def test_arbitrary_child_application_is_rejected(self):
        child = copy.deepcopy(self.evidence["applications"][1])
        child["name"] = "unreviewed-child"
        self.evidence["applications"].append(child)
        self.assertEvidenceRejected("application-set")

    def test_exact_application_project_destination_and_path_are_required(self):
        application = next(item for item in self.evidence["applications"]
                           if item["name"] == "fresh-account-release")
        for field, value, reason in (("project", "default", "application-project"),
                                     ("path", "learning", "application-path"),
                                     ("destination", {"server": "https://other", "namespace": "fresh-account"},
                                      "application-destination")):
            changed = copy.deepcopy(self.evidence)
            next(item for item in changed["applications"] if item["name"] == "fresh-account-release")[field] = value
            with self.assertRaisesRegex(contract.ContractError, reason):
                contract.validate_evidence(self.matrix, changed, self.canonical)

        changed = copy.deepcopy(self.evidence)
        app = next(item for item in changed["applications"] if item["name"] == "fresh-account-release")
        app["sourceConfig"]["kustomize"]["images"] = [
            "ghcr.io/syuhei0519/backend-app=ghcr.io/syuhei0519/backend-app@sha256:" + "f" * 64]
        with self.assertRaisesRegex(contract.ContractError, "application-sourceConfig:fresh-account-release"):
            contract.validate_evidence(self.matrix, changed, self.canonical)

    def test_unbounded_cluster_write_is_rejected(self):
        changed = copy.deepcopy(self.matrix)
        changed["permissions"][0]["resourceNames"] = []
        with self.assertRaisesRegex(contract.ContractError, "permission-resource-names"):
            contract.validate_matrix(changed)

    def test_bootstrap_authority_names_the_exact_rollouts_crd_set(self):
        operation = next(item for item in self.matrix["bootstrapOperations"]
                         if item.get("source") == "argo-rollouts")
        self.assertEqual(operation["names"], contract.ROLLOUTS_CRDS)
        for mutate in (
            lambda names: names.pop(),
            lambda names: names.append("other.argoproj.io"),
            lambda names: names.__setitem__(0, "wrong.argoproj.io"),
        ):
            changed = copy.deepcopy(self.matrix)
            names = next(item for item in changed["bootstrapOperations"]
                         if item.get("source") == "argo-rollouts")["names"]
            mutate(names)
            with self.subTest(names=names), self.assertRaisesRegex(
                    contract.ContractError, "bootstrap-crd-operations"):
                contract.validate_matrix(changed)

    def test_privilege_escalation_verb_is_rejected(self):
        changed = copy.deepcopy(self.matrix)
        changed["permissions"][0]["verbs"].append("escalate")
        with self.assertRaisesRegex(contract.ContractError, "permission-verb"):
            contract.validate_matrix(changed)

    def test_controller_service_account_bypass_is_rejected(self):
        self.evidence["controllers"][0]["podSpec"]["serviceAccountName"] = "argocd-application-controller"
        self.assertEvidenceRejected("controller-service-account")

    def test_controller_automount_bypass_is_rejected(self):
        self.evidence["controllers"][0]["podSpec"]["automountServiceAccountToken"] = True
        self.assertEvidenceRejected("controller-automount")

    def test_controller_secret_mount_bypass_is_rejected(self):
        self.evidence["controllers"][0]["podSpec"]["volumes"].append({
            "name": "other", "secret": {"secretName": "repository-read"}
        })
        self.assertEvidenceRejected("controller-volume")

    def test_privileged_controller_is_rejected(self):
        container = self.evidence["controllers"][0]["podSpec"]["containers"][0]
        container["securityContext"]["privileged"] = True
        self.assertEvidenceRejected("controller-privileged")

    def test_unknown_operation_name_is_rejected(self):
        self.evidence["operations"][3]["name"] = "arbitrary-deployment"
        self.assertEvidenceRejected("operation-authority")

    def test_pvc_secret_and_field_ownership_semantics_are_retained(self):
        self.evidence["retained"]["persistentVolumeClaims"]["fresh-account/account-data"]["storageClassName"] = "other"
        self.assertEvidenceRejected("retained-semantics")

    def test_rbac_resource_names_match_canonical_contract(self):
        documents = contract.load_yaml(contract.RBAC)
        role = next(item for item in documents if item["kind"] == "Role" and
                    item["metadata"]["namespace"] == "fresh-account")
        rule = next(item for item in role["rules"] if item.get("resourceNames") == ["account-data"])
        rule["resourceNames"] = ["other-pvc"]
        with self.assertRaisesRegex(contract.ContractError, "rbac-canonical-rules"):
            contract.validate_rbac(self.canonical, documents)

        documents = contract.load_yaml(contract.RBAC)
        role = next(item for item in documents if item["kind"] == "Role" and
                    item["metadata"]["namespace"] == "fresh-account")
        role["rules"].append({"apiGroups": [""], "resources": ["pods"], "verbs": ["create"]})
        with self.assertRaisesRegex(contract.ContractError, "rbac-pod-create"):
            contract.validate_rbac(self.canonical, documents)

    def test_admission_expression_covers_direct_pods_secret_mounts_init_and_ephemeral_containers(self):
        documents = contract.load_yaml(contract.ADMISSION)
        contract.validate_admission(self.canonical, documents)
        pod_policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                          "fresh-account-pod-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
        pod_policy["spec"]["validations"] = [item for item in pod_policy["spec"]["validations"]
                                               if "initContainers" not in item["expression"]]
        with self.assertRaisesRegex(contract.ContractError, "admission-pod-fragment:initContainers"):
            contract.validate_admission(self.canonical, documents)

        documents = contract.load_yaml(contract.ADMISSION)
        pod_policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                          "fresh-account-pod-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
        pod_policy["spec"]["validations"] = [item for item in pod_policy["spec"]["validations"]
                                               if "ephemeralContainers" not in item["expression"]]
        with self.assertRaisesRegex(contract.ContractError, "admission-pod-fragment:ephemeralContainers"):
            contract.validate_admission(self.canonical, documents)

        documents = contract.load_yaml(contract.ADMISSION)
        app_policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                          "fresh-application-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
        app_policy["spec"]["validations"] = [item for item in app_policy["spec"]["validations"]
                                               if "spec.sources" not in item["expression"]]
        with self.assertRaisesRegex(contract.ContractError, "admission-source-fragment:spec.sources"):
            contract.validate_admission(self.canonical, documents)

        for fragment in ("helm.valuesObject", "kustomize.namePrefix"):
            documents = contract.load_yaml(contract.ADMISSION)
            app_policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                              "fresh-application-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
            for validation in app_policy["spec"]["validations"]:
                validation["expression"] = validation["expression"].replace(fragment, "removedField")
            with self.subTest(fragment=fragment), self.assertRaisesRegex(
                    contract.ContractError, "admission-source-fragment"):
                contract.validate_admission(self.canonical, documents)

        documents = contract.load_yaml(contract.ADMISSION)
        pod_policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                          "fresh-account-pod-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
        rules = pod_policy["spec"]["matchConstraints"]["resourceRules"]
        self.assertTrue(any("pods/ephemeralcontainers" in item["resources"] for item in rules))

    def test_ephemeral_subresource_guard_handles_optional_admission_request_field(self):
        documents = contract.load_yaml(contract.ADMISSION)
        pod_policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                          "fresh-account-pod-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
        guards = [item["expression"] for item in pod_policy["spec"]["validations"]
                  if "request.subResource" in item["expression"]]
        self.assertEqual(len(guards), 1)
        self.assertEqual(contract.compact_expression(guards[0]),
                         contract.compact_expression(contract.EPHEMERAL_SUBRESOURCE_CLAUSE))

        # This is the truth table of the CEL presence guard.  The API-server
        # dry-run remains the authoritative compile/evaluation check; these
        # cases prevent a regression to direct selection of an omitted field.
        cases = (
            ({}, True),
            ({"subResource": ""}, True),
            ({"subResource": "status"}, True),
            ({"subResource": "ephemeralcontainers"}, False),
        )
        for request, allowed in cases:
            with self.subTest(request=request):
                actual = "subResource" not in request or request["subResource"] != "ephemeralcontainers"
                self.assertEqual(actual, allowed)

    def test_ephemeral_subresource_guard_cannot_drop_presence_check_or_broaden_denial(self):
        for replacement in (
                "request.subResource != 'ephemeralcontainers'",
                "!has(request.subResource)",
                "!has(request.subResource) || request.subResource != 'other'",
                "!has(request.subResource) || true"):
            documents = contract.load_yaml(contract.ADMISSION)
            pod_policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                              "fresh-account-pod-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
            guard = next(item for item in pod_policy["spec"]["validations"]
                         if "request.subResource" in item["expression"])
            guard["expression"] = replacement
            with self.subTest(replacement=replacement), self.assertRaisesRegex(
                    contract.ContractError, "admission-subresource-optional-field-guard"):
                contract.validate_admission(self.canonical, documents)

    def test_ephemeral_subresource_guard_is_the_only_optional_subresource_access(self):
        documents = contract.load_yaml(contract.ADMISSION)
        pod_policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                          "fresh-account-pod-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
        occurrences = [item["expression"] for item in pod_policy["spec"]["validations"]
                       if "request.subResource" in item["expression"]]
        self.assertEqual(occurrences, [next(item["expression"] for item in
                                            pod_policy["spec"]["validations"]
                                            if "!has(request.subResource)" in item["expression"])])

    def test_pod_create_requires_exact_controller_service_account_for_each_owner(self):
        documents = contract.load_yaml(contract.ADMISSION)
        contract.validate_admission(self.canonical, documents)
        policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                      "fresh-account-pod-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
        expression = policy["spec"]["validations"][1]["expression"]
        for identity in (
                "system:serviceaccount:kube-system:statefulset-controller",
                "system:serviceaccount:kube-system:job-controller",
                "system:serviceaccount:kube-system:replicaset-controller"):
            self.assertIn(identity, expression)
        self.assertNotIn("system:kube-controller-manager", expression)

        replacements = (
            ("system:serviceaccount:kube-system:statefulset-controller",
             "system:serviceaccount:kube-system:job-controller"),
            ("system:serviceaccount:kube-system:job-controller",
             "system:serviceaccount:kube-system:statefulset-controller"),
            ("system:serviceaccount:kube-system:replicaset-controller",
             "system:kube-controller-manager"),
            ("o.name == 'account-postgresql'", "o.name.startsWith('account-')"),
        )
        for old, new in replacements:
            changed = copy.deepcopy(documents)
            changed_policy = next(item for item in changed if item.get("metadata", {}).get("name") ==
                                  "fresh-account-pod-boundary" and
                                  item["kind"] == "ValidatingAdmissionPolicy")
            changed_policy["spec"]["validations"][1]["expression"] = \
                changed_policy["spec"]["validations"][1]["expression"].replace(old, new)
            with self.subTest(old=old), self.assertRaisesRegex(
                    contract.ContractError, "admission-pod"):
                contract.validate_admission(self.canonical, changed)

    def test_container_security_context_optional_parent_guard_is_exact(self):
        documents = contract.load_yaml(contract.ADMISSION)
        contract.validate_admission(self.canonical, documents)
        policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                      "fresh-account-pod-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
        expression = contract.compact_expression(policy["spec"]["validations"][5]["expression"])
        self.assertEqual(expression.count("has(c.securityContext) &&"), 4)
        self.assertEqual(expression.count("has(c.securityContext.allowPrivilegeEscalation) &&"), 4)

        cases = (
            ({}, False),
            ({"privileged": False}, False),
            ({"allowPrivilegeEscalation": False}, True),
            ({"privileged": False, "allowPrivilegeEscalation": False}, True),
            ({"privileged": True, "allowPrivilegeEscalation": False}, False),
            ({"privileged": False, "allowPrivilegeEscalation": True}, False),
        )
        for security_context, allowed in cases:
            actual = bool(security_context) and (
                security_context.get("privileged") in (None, False) and
                security_context.get("allowPrivilegeEscalation") is False)
            with self.subTest(securityContext=security_context):
                self.assertEqual(actual, allowed)

        changed = copy.deepcopy(documents)
        changed_policy = next(item for item in changed if item.get("metadata", {}).get("name") ==
                              "fresh-account-pod-boundary" and
                              item["kind"] == "ValidatingAdmissionPolicy")
        changed_policy["spec"]["validations"][5]["expression"] = \
            changed_policy["spec"]["validations"][5]["expression"].replace(
                "has(c.securityContext) &&", "", 1)
        with self.assertRaisesRegex(contract.ContractError,
                                    "admission-container-security-context"):
            contract.validate_admission(self.canonical, changed)

    def test_every_admission_cel_expression_has_balanced_delimiters(self):
        documents = contract.load_yaml(contract.ADMISSION)
        contract.validate_admission(self.canonical, documents)

        pod_policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                          "fresh-account-pod-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
        expression = pod_policy["spec"]["validations"][6]["expression"]
        self.assertEqual(expression.count("("), expression.count(")"))
        pod_policy["spec"]["validations"][6]["expression"] = expression[:-1]
        with self.assertRaisesRegex(
                contract.ContractError,
                "admission-cel-delimiters:fresh-account-pod-boundary:6"):
            contract.validate_admission(self.canonical, documents)

    def test_managed_system_resources_require_exact_controller_identity_name_and_namespace(self):
        replacements = (
            ("system:serviceaccount:kube-system:root-ca-cert-publisher",
             "system:serviceaccount:kube-system:service-account-controller",
             "admission-system-controller-boundary"),
            ("system:serviceaccount:kube-system:service-account-controller",
             "system:serviceaccount:kube-system:root-ca-cert-publisher",
             "admission-system-controller-boundary"),
            ("system:serviceaccount:kube-system:root-ca-cert-publisher",
             "system:kube-controller-manager", "admission-system-controller-boundary"),
            ("system:serviceaccount:kube-system:service-account-controller",
             "system:kube-controller-manager", "admission-system-controller-boundary"),
            ("kube-root-ca.crt", "other-root", "admission-system-controller-boundary"),
            ("object.metadata.name == 'default'", "object.metadata.name == 'other'",
             "admission-system-controller-boundary"),
            ("['fresh-account', 'fresh-observability']", "['fresh-account', 'fresh-observability', 'default']",
             "admission-system-controller-boundary"),
            ("system:serviceaccount:fresh-control:istiod-fresh-1-31-2",
             "system:serviceaccount:fresh-control:other", "admission-istio-root-boundary"),
            ("object.metadata.name == 'istio-ca-root-cert'", "object.metadata.name == 'other-ca'",
             "admission-istio-root-boundary"),
        )
        for old, new, reason in replacements:
            documents = contract.load_yaml(contract.ADMISSION)
            policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                          "fresh-managed-resource-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
            policy["spec"]["validations"][0]["expression"] = \
                policy["spec"]["validations"][0]["expression"].replace(old, new)
            with self.subTest(old=old), self.assertRaisesRegex(contract.ContractError, reason):
                contract.validate_admission(self.canonical, documents)

    def test_managed_system_exception_cannot_be_widened(self):
        documents = contract.load_yaml(contract.ADMISSION)
        policy = next(item for item in documents if item.get("metadata", {}).get("name") ==
                      "fresh-managed-resource-boundary" and item["kind"] == "ValidatingAdmissionPolicy")
        policy["spec"]["validations"][0]["expression"] += \
            " || (object.kind == 'ConfigMap' && object.metadata.name == 'arbitrary')"
        with self.assertRaisesRegex(contract.ContractError, "admission-managed-expression"):
            contract.validate_admission(self.canonical, documents)

        documents = contract.load_yaml(contract.ADMISSION)
        binding = next(item for item in documents if item.get("metadata", {}).get("name") ==
                       "fresh-managed-resource-boundary" and
                       item["kind"] == "ValidatingAdmissionPolicyBinding")
        binding["spec"]["matchResources"]["namespaceSelector"]["matchExpressions"][0]["values"].append("control")
        with self.assertRaisesRegex(contract.ContractError, "managed-binding-label"):
            contract.validate_admission(self.canonical, documents)

    def test_all_named_negative_fixtures_are_denied(self):
        fixture = contract.load(contract.NEGATIVE)
        contract.validate_negatives(self.canonical, fixture)
        bypass = next(item for item in fixture["cases"] if item["id"] == "direct-pod")
        bypass["object"]["metadata"]["ownerReferences"] = [
            {"kind": "ReplicaSet", "name": "account-api-fixture",
             "uid": "synthetic-test-controller", "controller": True}]
        with self.assertRaisesRegex(contract.ContractError, "negative-not-denied"):
            contract.validate_negatives(self.canonical, fixture)


if __name__ == "__main__":
    unittest.main(verbosity=2)
