#!/usr/bin/env python3
"""Validate the small supply inventory and emit a workflow matrix.

This never resolves or invents a destination digest.  A consumer may move to
GHCR only from the receipt produced after an actual byte-preserving OCI copy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "supply/images.json"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
DELIVERY_RUNS = {
    "argocd": "40934558588",
    "argo-rollouts": "40916984294",
    "istio-pilot": "40936986084",
    "istio-proxy": "40966477911",
    "prometheus": "40976122866",
    "prometheus-config-reloader": "40976337755",
    "otel-collector": "40934582025",
}
CANDIDATES = {
    "gitlab-runner-v19.3.5": {
        "name": "gitlab-runner-v19-3-5-candidate",
        "source": "registry.gitlab.com/gitlab-org/gitlab-runner:alpine-v19.3.5",
        "consumer": "read-only candidate for environments/local/gitlab-runner.yaml",
    },
    "gitlab-runner-helper-v19.3.5": {
        "name": "gitlab-runner-helper-v19-3-5-candidate",
        "source": ("registry.gitlab.com/gitlab-org/ci-cd/gitlab-runner-ubi-images/"
                   "gitlab-runner-helper-ocp:x86_64-v19.3.5"),
        "consumer": "read-only candidate for bootstrap/versions.lock.yaml runner helper",
    },
    "gitlab-runner-helper-alpine-v19.3.5": {
        "name": "gitlab-runner-helper-alpine-v19-3-5-candidate",
        "source": ("registry.gitlab.com/gitlab-org/gitlab-runner/"
                   "gitlab-runner-helper:x86_64-v19.3.5"),
        "consumer": "read-only compatibility candidate for the runner helper",
    },
}


def require(value, message):
    if not value:
        raise ValueError(message)


def load():
    data = json.loads(INVENTORY.read_text(), object_pairs_hook=_unique)
    require(data["schemaVersion"] == "platform.lab/image-supply/v1", "schema")
    require(data["targetArchitecture"] == "linux/amd64", "architecture")
    destination = data["destinationRepository"]
    require(destination == "ghcr.io/syuhei0519/platform-lab-images", "destination")
    require(data["policy"] == {
        "copyMode": "oci-byte-preserving",
        "redistributionApproval": "required-at-dispatch",
        "consumerCutover": "only-after-recorded-destination-digest",
        "offlineImport": "explicit-exception-only",
    }, "policy")
    require(data.get("accountDelivery") == {
        "runUrl": "https://github.com/syuhei0519/backend-app/actions/runs/40924904358",
        "destination": "ghcr.io/syuhei0519/backend-app",
        "destinationDigest":
            "sha256:fba8eae42982a0e818581b2ac6b41ba865920dafdbfd7d48c416f98c5b1eaebd",
    }, "account-delivery")
    evidence = data.get("redistributionEvidence", {})
    require(evidence == {
        "argocd": "supply/licenses/argocd/v3.5.2/evidence.json",
        "argo-rollouts": "supply/licenses/argo-rollouts/v1.10.1/evidence.json",
        "istio-pilot": "supply/licenses/istio/1.31.2/evidence.json",
        "istio-proxy": "supply/licenses/istio/1.31.2/evidence.json",
        "prometheus": "supply/licenses/prometheus/v3.15.0/evidence.json",
        "prometheus-config-reloader":
            "supply/licenses/prometheus-config-reloader/v0.94.1/evidence.json",
        "otel-collector": "supply/licenses/otel-collector/0.161.0/evidence.json",
    }, "redistribution-evidence")
    seen_names, seen_tags, matrix = set(), set(), []
    for item in data["images"]:
        require(set(item) == {"name", "source", "destinationTag", "producer", "consumer"}, "fields")
        require(NAME.fullmatch(item["name"]) and item["name"] not in seen_names, "name")
        require(TAG.fullmatch(item["destinationTag"]) and item["destinationTag"] not in seen_tags, "tag")
        source, separator, digest = item["source"].rpartition("@")
        require(separator and DIGEST.fullmatch(digest), "source-digest")
        require(":" in source.rsplit("/", 1)[-1], "source-tag")
        require(item["producer"] == "external-image-mirror" and item["consumer"], "mapping")
        seen_names.add(item["name"]); seen_tags.add(item["destinationTag"])
        matrix.append({**item, "sourceDigest": digest,
                       "destination": destination + ":" + item["destinationTag"]})
    require(len(matrix) == 14, "inventory-count")
    _validate_evidence(evidence, matrix)
    _validate_deliveries(data.get("deliveries", {}), evidence, matrix)
    _validate_consumers(data.get("consumerReferences", {}),
                        data.get("deliveries", {}), matrix)
    return matrix


def _validate_evidence(entries, matrix):
    by_name = {item["name"]: item for item in matrix}
    for name, relative in entries.items():
        path = ROOT / relative
        record = json.loads(path.read_text(), object_pairs_hook=_unique)
        require(record["schemaVersion"] == "platform.lab/redistribution-evidence/v1", "evidence-schema")
        require(record["redistributionReady"] is True, "evidence-ready")
        require(record["copyMode"] == "oci-byte-preserving", "evidence-copy-mode")
        if "images" in record:
            image = next((item for item in record["images"] if item["name"] == name), None)
            require(image is not None, "evidence-name")
            require(image["reference"] == by_name[name]["source"], "evidence-source")
            require(image["manifestDigest"] == by_name[name]["sourceDigest"], "evidence-digest")
            require(image["mediaType"] ==
                    "application/vnd.docker.distribution.manifest.v2+json", "evidence-media-type")
            require(image["platform"] == data_architecture(), "evidence-platform")
        else:
            require(record["name"] == name, "evidence-name")
            require(record["image"]["reference"] == by_name[name]["source"], "evidence-source")
            require(record["image"]["indexDigest"] == by_name[name]["sourceDigest"],
                    "evidence-digest")
        license_record = record["license"]
        license_path = path.parent / license_record["localPath"]
        require(license_record["spdx"] == "Apache-2.0", "evidence-license")
        require(hashlib.sha256(license_path.read_bytes()).hexdigest() == license_record["sha256"],
                "evidence-license-digest")
        notice = license_record["upstreamNotice"]
        if isinstance(notice, dict):
            notice_path = path.parent / notice["localPath"]
            require(hashlib.sha256(notice_path.read_bytes()).hexdigest() == notice["sha256"],
                    "evidence-notice-digest")


def _validate_deliveries(deliveries, evidence, matrix):
    """Bind successful public copies to concrete normal-consumer files.

    The copy is byte preserving, so the recorded destination digest must be
    the fixed upstream digest.  This deliberately does not make pending
    inventory entries look delivered.
    """
    require(set(deliveries) == set(evidence), "delivery-evidence-set")
    by_name = {item["name"]: item for item in matrix}
    for name, delivery in deliveries.items():
        require(set(delivery) == {"runUrl", "destinationDigest"},
                "delivery-fields")
        item = by_name[name]
        require(delivery["destinationDigest"] == item["sourceDigest"],
                "delivery-digest")
        require(re.fullmatch(
            r"https://github\.com/syuhei0519/platform-gitops/actions/runs/[0-9]+",
            delivery["runUrl"]), "delivery-run")
        require(delivery["runUrl"].endswith("/" + DELIVERY_RUNS[name]),
                "delivery-run-receipt")


def _validate_consumers(references, deliveries, matrix):
    by_name = {item["name"]: item for item in matrix}
    require(set(references) == set(by_name), "consumer-inventory-set")
    repository = "ghcr.io/syuhei0519/platform-lab-images"
    for name, consumers in references.items():
        require(isinstance(consumers, list) and consumers, "consumer-references")
        item = by_name[name]
        for consumer in consumers:
            require(set(consumer) == {"path", "type", "identity"},
                    "delivery-consumer-fields")
            relative = consumer["path"]
            require(isinstance(relative, str) and not relative.startswith("/"),
                    "delivery-consumer-path")
            actual = _consumer_image(ROOT / relative, consumer["type"], consumer["identity"])
            if name in deliveries:
                digest = deliveries[name]["destinationDigest"]
                tagged = repository + ":" + item["destinationTag"] + "@" + digest
                tagless = repository + "@" + digest
                require(actual in {tagged, tagless},
                        "delivery-consumer-reference:" + name + ":" + relative)
            else:
                require(not actual.startswith(repository)
                        and actual.endswith("@" + item["sourceDigest"]),
                        "undelivered-consumer-reference:" + name + ":" + relative)


def _consumer_image(path, consumer_type, identity):
    docs = list(yaml.safe_load_all(path.read_text()))
    if consumer_type == "profile-image":
        require(len(docs) == 1, "consumer-profile-docs")
        matches = [item["ref"] for item in docs[0]["spec"]["images"]
                   if item["name"] == identity]
        require(len(matches) == 1, "consumer-profile-identity")
        return matches[0]
    if consumer_type == "deployment-image":
        matches = [doc for doc in docs if doc and doc.get("kind") == "Deployment"
                   and doc.get("metadata", {}).get("name") == identity]
        require(len(matches) == 1, "consumer-deployment-identity")
        containers = matches[0]["spec"]["template"]["spec"]["containers"]
        require(len(containers) == 1, "consumer-deployment-containers")
        return containers[0]["image"]
    require(len(docs) == 1, "consumer-values-docs")
    value = docs[0]
    for key in identity.split("."):
        require(isinstance(value, dict) and key in value, "consumer-values-identity")
        value = value[key]
    if consumer_type == "value-image":
        require(isinstance(value, str), "consumer-value-image")
        return value
    if consumer_type == "split-image":
        require(isinstance(value, dict)
                and set(("repository", "tag", "digest")).issubset(value),
                "consumer-split-image")
        return value["repository"] + ":" + value["tag"] + "@" + value["digest"]
    if consumer_type == "registry-split-image":
        require(isinstance(value, dict)
                and set(("registry", "repository", "tag", "sha")).issubset(value),
                "consumer-registry-split-image")
        return (value["registry"] + "/" + value["repository"] + ":"
                + value["tag"] + "@" + value["sha"])
    if consumer_type == "grafana-image":
        require(isinstance(value, dict)
                and set(("registry", "repository", "tag", "sha")).issubset(value),
                "consumer-grafana-image")
        return (value["registry"] + "/" + value["repository"] + ":"
                + value["tag"] + "@sha256:" + value["sha"])
    if consumer_type == "runner-image":
        require(isinstance(value, dict)
                and set(("registry", "image", "tag")).issubset(value),
                "consumer-runner-image")
        return value["registry"] + "/" + value["image"] + ":" + value["tag"]
    raise ValueError("consumer-type")


def data_architecture():
    return "linux/amd64"


def select_ready(matrix, selection):
    data = json.loads(INVENTORY.read_text(), object_pairs_hook=_unique)
    ready = set(data.get("redistributionEvidence", {}))
    if selection == "all":
        selected = [item for item in matrix if item["name"] in ready]
        require(selected and {item["name"] for item in selected} == ready,
                "ready-inventory-mismatch")
        return selected
    require(selection in ready, "image-not-redistribution-ready")
    selected = [item for item in matrix if item["name"] == selection]
    require(len(selected) == 1, "unknown-image")
    return selected


def select_for_inspection(matrix, selection):
    """Select one exact inventory entry without making it redistribution-ready.

    ``all`` deliberately retains its existing meaning (the approved set only),
    while an explicit name lets the read-only inspection job collect OCI
    identity needed to review a not-yet-approved entry.
    """
    if selection == "all":
        return select_ready(matrix, selection)
    selected = [item for item in matrix if item["name"] == selection]
    require(len(selected) == 1, "unknown-image")
    return selected


def select_candidate(selection):
    """Return one static, non-publishable candidate for read-only inspection."""
    require(selection in CANDIDATES, "unknown-candidate")
    return [{**CANDIDATES[selection], "sourceDigest": "", "candidate": True,
             "destination": "read-only-candidate-not-publishable"}]


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate-key")
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--select", default="all")
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--candidate")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    inventory = load()
    if args.candidate:
        require(args.inspect_only, "candidate-inspect-only")
        matrix = select_candidate(args.candidate)
    else:
        matrix = (select_for_inspection(inventory, args.select) if args.inspect_only
                  else select_ready(inventory, args.select))
    encoded = json.dumps({"include": matrix}, separators=(",", ":"), sort_keys=True)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as output:
            output.write("matrix=" + encoded + "\n")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
