#!/usr/bin/env python3
"""Render the pinned Argo Rollouts CRDs and prove controller/CRD separation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import yaml
from plugin_overlay import overlay


CHART_VERSION = "2.43.7"
CHART_BYTES = 68002
CHART_SHA256 = "166506174f4be91ed2758fccbca340e209cba35aa72ac08b5863a7b797942487"
CRD_LIST_SHA256 = "9c4794e462b522f45077f0dabaf39df1ba3a35d29ec28787f6007ea989ddf07b"
CONTROLLER_IMAGE_TAG = (
    "v1.10.1@sha256:"
    "fbcc4cf0a29da25d86230992b1dfdeadf30b6f1baa74ea7c9c98e3e580769396"
)
CONTROLLER_IMAGE = "quay.io/argoproj/argo-rollouts:" + CONTROLLER_IMAGE_TAG
CRD_TEMPLATES = (
    "analysis-run-crd.yaml",
    "analysis-template-crd.yaml",
    "cluster-analysis-template-crd.yaml",
    "experiment-crd.yaml",
    "rollout-crd.yaml",
)
CRD_NAMES = {
    "analysisruns.argoproj.io",
    "analysistemplates.argoproj.io",
    "clusteranalysistemplates.argoproj.io",
    "experiments.argoproj.io",
    "rollouts.argoproj.io",
}


class RenderError(ValueError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_chart(chart: Path) -> None:
    if not chart.is_file() or chart.is_symlink():
        raise RenderError("chart-file")
    if chart.stat().st_size != CHART_BYTES:
        raise RenderError("chart-bytes")
    if sha256(chart) != CHART_SHA256:
        raise RenderError("chart-sha256")


def parse_documents(rendered: str) -> list[dict]:
    try:
        documents = [item for item in yaml.safe_load_all(rendered) if item is not None]
    except yaml.YAMLError as error:
        raise RenderError("helm-yaml") from error
    if not documents or any(not isinstance(item, dict) for item in documents):
        raise RenderError("helm-documents")
    return documents


def validate_crds(documents: list[dict]) -> list[dict]:
    if len(documents) != len(CRD_NAMES):
        raise RenderError("crd-count")
    names = set()
    for document in documents:
        metadata = document.get("metadata")
        if (document.get("apiVersion") != "apiextensions.k8s.io/v1"
                or document.get("kind") != "CustomResourceDefinition"
                or not isinstance(metadata, dict)
                or not isinstance(metadata.get("name"), str)
                or metadata.get("namespace") is not None):
            raise RenderError("crd-identity")
        names.add(metadata["name"])
    if names != CRD_NAMES:
        raise RenderError("crd-name-set")
    return sorted(documents, key=lambda item: item["metadata"]["name"])


def reject_controller_crds(documents: list[dict]) -> None:
    if any(item.get("kind") == "CustomResourceDefinition" for item in documents):
        raise RenderError("controller-render-contains-crd")


def validate_controller(documents: list[dict]) -> None:
    reject_controller_crds(documents)
    deployments = [item for item in documents
                   if item.get("apiVersion") == "apps/v1"
                   and item.get("kind") == "Deployment"
                   and item.get("metadata", {}).get("name") == "argo-rollouts"
                   and item.get("metadata", {}).get("namespace") == "fresh-control"]
    if len(deployments) != 1:
        raise RenderError("controller-deployment")
    containers = deployments[0].get("spec", {}).get("template", {}).get(
        "spec", {}).get("containers")
    if (not isinstance(containers, list) or len(containers) != 1
            or containers[0].get("image") != CONTROLLER_IMAGE):
        raise RenderError("controller-image")


def run_helm(helm: Path, arguments: list[str]) -> str:
    if not helm.is_file() or helm.is_symlink():
        raise RenderError("helm-file")
    completed = subprocess.run(
        [str(helm), *arguments], check=False, capture_output=True, text=True,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise RenderError("helm-exit:" + str(completed.returncode))
    return completed.stdout


def render(helm: Path, chart: Path, output: Path) -> dict:
    verify_chart(chart)
    base = ["template", "argo-rollouts", str(chart.resolve()),
            "--namespace", "fresh-control"]
    crd_arguments = [*base, "--set", "installCRDs=true"]
    for template in CRD_TEMPLATES:
        crd_arguments.extend(["--show-only", "templates/crds/" + template])
    crds = validate_crds(parse_documents(run_helm(helm, crd_arguments)))

    controller_arguments = [*base, "--set", "installCRDs=false", "--set-string",
                            "controller.image.tag=" + CONTROLLER_IMAGE_TAG]
    controller_documents = parse_documents(run_helm(helm, controller_arguments))
    validate_controller(controller_documents)

    result = {"apiVersion": "v1", "kind": "List", "items": crds}
    encoded = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
    rendered_digest = hashlib.sha256(encoded).hexdigest()
    if rendered_digest != CRD_LIST_SHA256:
        raise RenderError("crd-list-sha256")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)
    return {
        "chartSha256": CHART_SHA256,
        "crdListSha256": rendered_digest,
        "crdNames": sorted(CRD_NAMES),
        "controllerObjectCount": len(controller_documents),
    }


def render_plugin_bundle(helm: Path, chart: Path, output: Path, image: str, binary_sha256: str) -> dict:
    """Render an offline, checksum-addressed proposal from the pinned Helm chart."""
    verify_chart(chart)
    args = ["template", "argo-rollouts", str(chart.resolve()), "--namespace", "fresh-control",
            "--set", "installCRDs=false", "--set-string", "controller.image.tag=" + CONTROLLER_IMAGE_TAG]
    docs = parse_documents(run_helm(helm, args))
    validate_controller(docs)
    configs = [d for d in docs if d.get('kind') == 'ConfigMap' and d.get('metadata', {}).get('name') == 'argo-rollouts-config' and d.get('metadata', {}).get('namespace') == 'fresh-control']
    if len(configs) != 1 or (configs[0].get('data') or {}).get('metricProviderPlugins'):
        raise RenderError('plugin-config-conflict')
    controller = next(d for d in docs if d.get('kind') == 'Deployment' and d.get('metadata', {}).get('name') == 'argo-rollouts')
    try:
        replaced, config, role, binding = overlay(controller, image, binary_sha256)
    except ValueError as exc:
        raise RenderError(str(exc)) from exc
    configs[0]['data'] = dict(configs[0].get('data') or {})
    configs[0]['data'].update(config['data'])
    docs = [replaced, configs[0], role, binding]
    encoded = (json.dumps({'apiVersion':'v1','kind':'List','items':docs},sort_keys=True,separators=(',',':'))+'\n').encode()
    output = output.resolve(); output.parent.mkdir(parents=True,exist_ok=True); output.write_bytes(encoded)
    return {'bundleSha256':hashlib.sha256(encoded).hexdigest(), 'controllerImage':image, 'binarySha256':binary_sha256}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helm", type=Path, required=True)
    parser.add_argument("--chart", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plugin-output", type=Path)
    parser.add_argument("--plugin-image")
    parser.add_argument("--plugin-sha256")
    args = parser.parse_args()
    try:
        result = render(args.helm, args.chart, args.output)
        if any((args.plugin_output, args.plugin_image, args.plugin_sha256)):
            if not all((args.plugin_output, args.plugin_image, args.plugin_sha256)):
                raise RenderError("plugin-inputs-required")
            result["plugin"] = render_plugin_bundle(args.helm, args.chart, args.plugin_output, args.plugin_image, args.plugin_sha256)
    except (OSError, UnicodeError, RenderError) as error:
        print("rollouts bootstrap render rejected: " + str(error))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
