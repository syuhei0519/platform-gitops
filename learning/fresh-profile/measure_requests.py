#!/usr/bin/env python3
"""Measure declared Pod requests from fixed fresh-profile YAML; no cluster I/O."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import yaml


HERE = Path(__file__).resolve().parent
OBSERVABILITY = HERE / "observability" / "resources.yaml"


class MeasurementError(ValueError):
    pass


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cpu_milli(value):
    text = str(value)
    if text.endswith("m") and text[:-1].isdigit():
        return int(text[:-1])
    if re.fullmatch(r"[0-9]+", text):
        return int(text) * 1000
    raise MeasurementError("unsupported-cpu-quantity:" + text)


def memory_mib(value):
    text = str(value)
    match = re.fullmatch(r"([0-9]+)(Ki|Mi|Gi)", text)
    if not match:
        raise MeasurementError("unsupported-memory-quantity:" + text)
    amount, unit = int(match.group(1)), match.group(2)
    if unit == "Ki" and amount % 1024:
        raise MeasurementError("fractional-mib:" + text)
    return {"Ki": amount // 1024, "Mi": amount, "Gi": amount * 1024}[unit]


def pod_template(document):
    if document.get("kind") in {"Deployment", "StatefulSet", "Job", "Rollout"}:
        return document["spec"]["template"]["spec"]
    return None


def measure(paths):
    cpu = memory = 0
    controllers = []
    inputs = {}
    for path in map(Path, paths):
        inputs[str(path).replace("\\", "/")] = sha256(path)
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            pod = pod_template(document)
            if not pod:
                continue
            replicas = 1 if document["kind"] == "Job" else document["spec"].get("replicas", 1)
            controller_cpu = controller_memory = 0
            for container in pod.get("initContainers", []) + pod.get("containers", []):
                requests = container.get("resources", {}).get("requests", {})
                if set(requests) != {"cpu", "memory"}:
                    raise MeasurementError(
                        f"requests-not-explicit:{document['kind']}/{document['metadata']['name']}/{container['name']}")
                controller_cpu += cpu_milli(requests["cpu"])
                controller_memory += memory_mib(requests["memory"])
            cpu += controller_cpu * replicas
            memory += controller_memory * replicas
            controllers.append({
                "identity": f"{document['kind']}/{document['metadata']['namespace']}/{document['metadata']['name']}",
                "replicas": replicas,
                "cpuMilli": controller_cpu * replicas,
                "memoryMiB": controller_memory * replicas,
            })
    return {"cpuMilli": cpu, "memoryMiB": memory, "controllers": controllers, "inputs": inputs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--observability", type=Path, default=OBSERVABILITY)
    args = parser.parse_args()
    try:
        result = measure([args.workload, args.observability])
    except (MeasurementError, OSError, yaml.YAMLError) as error:
        print(f"request measurement rejected: {error}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
