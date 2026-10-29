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

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "supply/images.json"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")


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


def data_architecture():
    return "linux/amd64"


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate-key")
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--select", default="all")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    matrix = load()
    if args.select != "all":
        matrix = [item for item in matrix if item["name"] == args.select]
        require(len(matrix) == 1, "unknown-image")
    encoded = json.dumps({"include": matrix}, separators=(",", ":"), sort_keys=True)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as output:
            output.write("matrix=" + encoded + "\n")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
