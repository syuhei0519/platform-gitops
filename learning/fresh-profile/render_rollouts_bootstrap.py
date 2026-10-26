#!/usr/bin/env python3
"""Render the pinned Argo Rollouts CRDs and prove controller/CRD separation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import yaml


CHART_VERSION = "2.43.7"
CHART_BYTES = 68002
CHART_SHA256 = "166506174f4be91ed2758fccbca340e209cba35aa72ac08b5863a7b797942487"
CRD_LIST_SHA256 = "9c4794e462b522f45077f0dabaf39df1ba3a35d29ec28787f6007ea989ddf07b"
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

    controller_arguments = [*base, "--set", "installCRDs=false"]
    controller_documents = parse_documents(run_helm(helm, controller_arguments))
    reject_controller_crds(controller_documents)

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helm", type=Path, required=True)
    parser.add_argument("--chart", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = render(args.helm, args.chart, args.output)
    except (OSError, UnicodeError, RenderError) as error:
        print("rollouts bootstrap render rejected: " + str(error))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
