import importlib.util
from pathlib import Path
import tempfile
import unittest

import yaml


MODULE = Path(__file__).with_name("validate_manifests.py")
SPEC = importlib.util.spec_from_file_location("validate_manifests", MODULE)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


class FreshManifestTests(unittest.TestCase):
    def temp(self, documents):
        stream = tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8",
                                             delete=False, dir=contract.HERE)
        yaml.safe_dump_all(documents, stream, sort_keys=False)
        stream.close()
        path = Path(stream.name)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_checked_manifests_are_finite(self):
        root, children, observability, rbac, admission = contract.validate()
        self.assertEqual((len(root), len(children), len(observability), len(rbac), len(admission)),
                         (7, 1, 11, 6, 6))

    def test_cluster_rbac_is_rejected(self):
        rbac = contract.load(contract.RBAC)
        rbac[0]["kind"] = "ClusterRole"
        with self.assertRaisesRegex(contract.Rejected, "cluster-rbac"):
            contract.validate(rbac_path=self.temp(rbac))

    def test_floating_root_revision_and_sonar_are_rejected(self):
        root = contract.load(contract.ROOT)
        next(item for item in root if item["kind"] == "Application")["spec"]["source"]["targetRevision"] = "main"
        with self.assertRaisesRegex(contract.Rejected, "application-revision:account-lightweight-root"):
            contract.validate(root_path=self.temp(root))
        observability = contract.load(contract.OBSERVABILITY)
        observability[0]["metadata"]["name"] = "sonarqube"
        with self.assertRaisesRegex(contract.Rejected, "sonar-duplicated"):
            contract.validate(observability_path=self.temp(observability))

    def test_namespace_security_labels_are_required(self):
        root = contract.load(contract.ROOT)
        namespace = next(item for item in root if item["kind"] == "Namespace" and
                         item["metadata"]["name"] == "fresh-account")
        namespace["metadata"]["labels"].pop("platform.lab/security-boundary", None)
        with self.assertRaisesRegex(contract.Rejected, "namespace-labels:fresh-account"):
            contract.validate(root_path=self.temp(root))

    def test_grafana_recovery_desired_state_is_exact(self):
        def grafana_container(documents):
            deployment = next(item for item in documents if item.get("kind") == "Deployment" and
                              item.get("metadata", {}).get("name") == "grafana")
            return deployment, deployment["spec"]["template"]["spec"]["containers"][0]

        observability = contract.load(contract.OBSERVABILITY)
        deployment, _ = grafana_container(observability)
        deployment["spec"]["strategy"] = {"type": "RollingUpdate"}
        with self.assertRaisesRegex(contract.Rejected, "grafana-recreate"):
            contract.validate(observability_path=self.temp(observability))

        observability = contract.load(contract.OBSERVABILITY)
        _, container = grafana_container(observability)
        container["resources"]["limits"]["memory"] = "256Mi"
        with self.assertRaisesRegex(contract.Rejected, "grafana-resources"):
            contract.validate(observability_path=self.temp(observability))

        observability = contract.load(contract.OBSERVABILITY)
        _, container = grafana_container(observability)
        container["env"][0]["value"] = "false"
        with self.assertRaisesRegex(contract.Rejected, "grafana-safe-env"):
            contract.validate(observability_path=self.temp(observability))

        observability = contract.load(contract.OBSERVABILITY)
        _, container = grafana_container(observability)
        container["readinessProbe"]["httpGet"]["path"] = "/"
        with self.assertRaisesRegex(contract.Rejected, "grafana-readiness"):
            contract.validate(observability_path=self.temp(observability))

        observability = contract.load(contract.OBSERVABILITY)
        _, container = grafana_container(observability)
        container["livenessProbe"] = container["readinessProbe"]
        with self.assertRaisesRegex(contract.Rejected, "grafana-no-auto-restart-probes"):
            contract.validate(observability_path=self.temp(observability))

    def test_child_project_and_destination_are_exact(self):
        children = contract.load(contract.CHILDREN)
        children[0]["spec"]["project"] = "default"
        with self.assertRaisesRegex(contract.Rejected, "application-project:fresh-release-definitions"):
            contract.validate(children_path=self.temp(children))

        children = contract.load(contract.CHILDREN)
        children[0]["spec"]["source"]["helm"]["parameters"][1]["value"] = (
            "ghcr.io/syuhei0519/backend-app@sha256:" + "f" * 64)
        with self.assertRaisesRegex(contract.Rejected, "application-source-config:fresh-release-definitions"):
            contract.validate(children_path=self.temp(children))

        children = contract.load(contract.CHILDREN)
        children[0]["spec"]["sources"] = [children[0]["spec"]["source"]]
        with self.assertRaisesRegex(contract.Rejected, "application-source-config:fresh-release-definitions"):
            contract.validate(children_path=self.temp(children))

    def test_cluster_registration_is_exact_and_noncredential(self):
        values = yaml.safe_load(contract.ARGOCD_VALUES.read_text(encoding="utf-8"))
        cluster = values["configs"]["clusterCredentials"]["learning-fresh-plat217-20261025"]
        cluster["clusterResources"] = "true"
        with self.assertRaisesRegex(contract.Rejected, "argocd-cluster-registration-resources"):
            contract.validate(argocd_values_path=self.temp([values]))

        values = yaml.safe_load(contract.ARGOCD_VALUES.read_text(encoding="utf-8"))
        cluster = values["configs"]["clusterCredentials"]["learning-fresh-plat217-20261025"]
        cluster["config"] = {"bearerToken": "must-not-exist"}
        with self.assertRaisesRegex(contract.Rejected, "argocd-cluster-registration-config"):
            contract.validate(argocd_values_path=self.temp([values]))

    def test_secret_and_respect_rbac_cannot_hide_required_resources(self):
        values = yaml.safe_load(contract.ARGOCD_VALUES.read_text(encoding="utf-8"))
        inclusions = yaml.safe_load(values["configs"]["cm"]["resource.inclusions"])
        inclusions[0]["kinds"].append("Secret")
        values["configs"]["cm"]["resource.inclusions"] = yaml.safe_dump(inclusions, sort_keys=False)
        with self.assertRaisesRegex(contract.Rejected, "argocd-cache-inclusions"):
            contract.validate(argocd_values_path=self.temp([values]))

        values = yaml.safe_load(contract.ARGOCD_VALUES.read_text(encoding="utf-8"))
        values["configs"]["cm"]["resource.respectRBAC"] = "normal"
        with self.assertRaisesRegex(contract.Rejected, "argocd-cache-respect-rbac"):
            contract.validate(argocd_values_path=self.temp([values]))

        values = yaml.safe_load(contract.ARGOCD_VALUES.read_text(encoding="utf-8"))
        values["configs"]["cm"]["resource.exclusions"] = "- apiGroups: [argoproj.io]\n  kinds: [Application]\n"
        with self.assertRaisesRegex(contract.Rejected, "argocd-cache-custom-exclusions"):
            contract.validate(argocd_values_path=self.temp([values]))

    def test_cluster_registration_has_no_project_shard_or_extra_field(self):
        values = yaml.safe_load(contract.ARGOCD_VALUES.read_text(encoding="utf-8"))
        cluster = values["configs"]["clusterCredentials"]["learning-fresh-plat217-20261025"]
        cluster["project"] = "fresh-platform"
        with self.assertRaisesRegex(contract.Rejected, "argocd-cluster-registration-fields"):
            contract.validate(argocd_values_path=self.temp([values]))

    def test_cache_read_union_is_required_in_every_namespace(self):
        rbac = contract.load(contract.RBAC)
        control_role = next(item for item in rbac if item["kind"] == "Role" and
                            item["metadata"]["namespace"] == "fresh-control")
        control_role["rules"].pop(0)
        with self.assertRaisesRegex(contract.Rejected, "rbac-canonical-rules"):
            contract.validate(rbac_path=self.temp(rbac))

    def test_controller_ready_alone_is_not_cache_acceptance(self):
        canonical = contract.authority.load(contract.CANONICAL)
        canonical["argocdCacheScope"]["acceptance"] = [
            "controller Pod Ready is necessary but not sufficient"
        ]
        path = tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8",
                                           delete=False, dir=contract.HERE)
        import json
        json.dump(canonical, path)
        path.close()
        canonical_path = Path(path.name)
        self.addCleanup(canonical_path.unlink, missing_ok=True)
        with self.assertRaisesRegex(contract.authority.ContractError,
                                    "canonical-argocd-cache-scope"):
            contract.validate(canonical_path=canonical_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
