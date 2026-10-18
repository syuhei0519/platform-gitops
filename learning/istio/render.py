#!/usr/bin/env python3
"""Offline rendering only. No Kubernetes client, apply, registry, or cluster access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parent


def pinned(path, record):
    data = Path(path).read_bytes()
    if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError("pinned bytes mismatch: " + str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helm", required=True)
    parser.add_argument("--istioctl", required=True)
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    args = parser.parse_args()
    lock = json.loads((ROOT / "versions.lock.json").read_text())
    pinned(args.helm, lock["helm"]["binary"])
    pinned(args.istioctl, lock["istio"]["istioctl"])
    for chart in lock["official_charts"].values():
        directory = ROOT / Path(chart["files"][0]["path"]).parent
        expected = {entry["path"] for entry in chart["files"]}
        actual = {str(path.relative_to(ROOT)) for path in directory.rglob("*") if path.is_file()}
        if actual != expected:
            raise ValueError("chart file inventory mismatch")
        for entry in chart["files"]:
            pinned(ROOT / entry["path"], entry)
    args.output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as temp:
        env = dict(os.environ, KUBECONFIG=temp + "/absent", HELM_CACHE_HOME=temp + "/cache",
                   HELM_CONFIG_HOME=temp + "/config", HELM_DATA_HOME=temp + "/data")

        def run(command):
            return subprocess.check_output(command, env=env, stderr=subprocess.PIPE)

        def helm(chart, namespace, values=None, mode=None, phase=None):
            command = [args.helm, "template", "mesh-demo" if chart == "chart" else
                       "istio-base" if chart.endswith("base") else "istiod",
                       str(ROOT / chart), "--namespace", namespace, "--kube-version", "1.36.4", "--include-crds"]
            if values:
                command += ["-f", str(ROOT / values)]
            if mode:
                command += ["--set", "mode=" + mode, "--set", "phase=" + phase]
            return run(command)

        (args.output / "base.yaml").write_bytes(helm("vendor/base", "istio-learning-system", "install/base.values.yaml"))
        (args.output / "istiod.yaml").write_bytes(helm("vendor/istiod", "istio-learning-system", "install/istiod.values.yaml"))
        for mode in ["stable", "split", "plain"]:
            (args.output / (mode + ".yaml")).write_bytes(helm("chart", "learning-istio-demo",
                mode="split" if mode == "split" else "stable", phase="plain" if mode == "plain" else "mesh"))
        for resource in yaml.safe_load_all((args.output / "istiod.yaml").read_text()):
            if resource and resource["kind"] == "ConfigMap":
                data, name = resource["data"], resource["metadata"]["name"]
                if name.startswith("istio-sidecar-injector-"):
                    Path(temp + "/config.yaml").write_text(data["config"])
                    Path(temp + "/values.json").write_text(data["values"])
                if name == "istio-learning-1-31-2":
                    Path(temp + "/mesh.yaml").write_text(data["mesh"])
        # All three configs are explicit local files; no webhook or kube lookup.
        run([args.istioctl, "kube-inject", "-f", str(args.output / "stable.yaml"),
             "--injectConfigFile", temp + "/config.yaml", "--valuesFile", temp + "/values.json",
             "--meshConfigFile", temp + "/mesh.yaml", "-i", "istio-learning-system", "-r", "learning-1-31-2",
             "-o", str(args.output / "injected.yaml")])
        for mode in ["stable", "split", "plain"]:
            ns = "bootstrap/plain.yaml" if mode == "plain" else "bootstrap/namespaces.yaml"
            run([args.istioctl, "analyze", "--use-kube=false", "--failure-threshold", "Warning",
                 str(ROOT / ns), str(args.output / (mode + ".yaml"))])
    print("PASS: pinned tools/charts, 5 renders, offline injection, 3 offline analyses")


if __name__ == "__main__":
    main()
