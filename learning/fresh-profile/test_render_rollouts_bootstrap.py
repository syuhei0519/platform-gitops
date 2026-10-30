#!/usr/bin/env python3
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "render_rollouts_bootstrap", HERE / "render_rollouts_bootstrap.py")
contract = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(contract)


def crd(name):
    return {
        "apiVersion": "apiextensions.k8s.io/v1",
        "kind": "CustomResourceDefinition",
        "metadata": {"name": name},
        "spec": {"group": "argoproj.io"},
    }


def controller(image=contract.CONTROLLER_IMAGE):
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": "argo-rollouts", "namespace": "fresh-control"},
        "spec": {"template": {"spec": {"containers": [
            {"name": "argo-rollouts", "image": image},
        ]}}},
    }


class RenderRolloutsBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.documents = [crd(name) for name in sorted(contract.CRD_NAMES)]

    def test_exact_five_crds_are_accepted_and_sorted(self):
        result = contract.validate_crds(list(reversed(self.documents)))
        self.assertEqual([item["metadata"]["name"] for item in result],
                         sorted(contract.CRD_NAMES))

    def test_missing_extra_and_wrong_crds_are_rejected(self):
        values = (
            self.documents[:-1],
            [*self.documents, crd("extra.argoproj.io")],
            [*self.documents[:-1], crd("wrong.argoproj.io")],
        )
        for value in values:
            with self.subTest(names=[item["metadata"]["name"] for item in value]):
                with self.assertRaises(contract.RenderError):
                    contract.validate_crds(value)

    def test_namespaced_or_non_crd_document_is_rejected(self):
        namespaced = copy.deepcopy(self.documents)
        namespaced[0]["metadata"]["namespace"] = "fresh-control"
        with self.assertRaisesRegex(contract.RenderError, "crd-identity"):
            contract.validate_crds(namespaced)
        wrong_kind = copy.deepcopy(self.documents)
        wrong_kind[0]["kind"] = "Deployment"
        with self.assertRaisesRegex(contract.RenderError, "crd-identity"):
            contract.validate_crds(wrong_kind)

    def test_controller_render_must_not_contain_a_crd(self):
        contract.reject_controller_crds([{"kind": "Deployment"}])
        with self.assertRaisesRegex(contract.RenderError, "controller-render-contains-crd"):
            contract.reject_controller_crds([{"kind": "Deployment"}, self.documents[0]])

    def test_controller_render_requires_the_fixed_digest_image(self):
        contract.validate_controller([controller()])
        for documents in (
            [controller("ghcr.io/syuhei0519/platform-lab-images:argo-rollouts-v1.10.1")],
            [controller(), controller()],
            [{"apiVersion": "apps/v1", "kind": "Deployment",
              "metadata": {"name": "other", "namespace": "fresh-control"}}],
        ):
            with self.subTest(documents=documents):
                with self.assertRaises(contract.RenderError):
                    contract.validate_controller(documents)

    def test_chart_bytes_and_digest_are_both_required(self):
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            path = Path(directory) / "chart.tgz"
            path.write_bytes(b"wrong-size")
            with self.assertRaisesRegex(contract.RenderError, "chart-bytes"):
                contract.verify_chart(path)
            path.write_bytes(b"0" * contract.CHART_BYTES)
            with self.assertRaisesRegex(contract.RenderError, "chart-sha256"):
                contract.verify_chart(path)

    def test_exact_five_but_noncanonical_crd_bytes_are_rejected(self):
        rendered_crds = "\n---\n".join(
            json.dumps(item) for item in self.documents)
        rendered_controller = json.dumps(controller())
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            output = Path(directory) / "crds.json"
            with mock.patch.object(contract, "verify_chart"), mock.patch.object(
                    contract, "run_helm",
                    side_effect=[rendered_crds, rendered_controller]):
                with self.assertRaisesRegex(contract.RenderError, "crd-list-sha256"):
                    contract.render(Path("helm"), Path("chart.tgz"), output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
