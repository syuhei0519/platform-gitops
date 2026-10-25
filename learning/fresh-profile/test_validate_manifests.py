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


if __name__ == "__main__":
    unittest.main(verbosity=2)
