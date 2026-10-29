#!/usr/bin/env python3
"""Validate the small supply inventory and emit a workflow matrix.

This never resolves or invents a destination digest.  A consumer may move to
GHCR only from the receipt produced after an actual byte-preserving OCI copy.
"""
import argparse
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
    return matrix


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
