#!/usr/bin/env python3
"""Bind any image-scan exception to its one approved immutable OCI identity."""
import json
from pathlib import Path
import sys


POSTGRES = {
    "name": "postgres-18",
    "source": ("docker.io/library/postgres:18.6-bookworm@sha256:"
               "bbf83594798e144bff365a9c191c5d779f181fb3135902064ab8ff11918020fc"),
    "topDigest": "sha256:bbf83594798e144bff365a9c191c5d779f181fb3135902064ab8ff11918020fc",
    "selectedManifestDigest":
        "sha256:6b38fe68f2c02cafffbbce7496f76d110d8a947c039bd22c067e9a43c2250240",
    "configDigest": "sha256:63d4ad6f3de5e288238b3044970a677dcb0e77004d8b6050732aa0cbbeedf5d7",
    "platform": "linux/amd64",
}
EXCEPTION = {
    "kind": "vulnerability",
    "rule": "CVE-2025-68121",
    "path": "usr/local/bin/gosu",
    "package": "stdlib",
    "severity": "CRITICAL",
    "decision": "excepted",
    "exception": "plat-229-postgres-gosu-go-2026-4337",
}


def validate(receipt, summary):
    excepted = [finding for finding in summary.get("findings", [])
                if finding.get("decision") == "excepted"]
    if not excepted:
        return
    if any(receipt.get(key) != value for key, value in POSTGRES.items()):
        raise ValueError("scan-exception-image-identity")
    if len(excepted) != 1 or any(
            excepted[0].get(key) != value for key, value in EXCEPTION.items()):
        raise ValueError("scan-exception-finding")
    if summary.get("pass") is not True:
        raise ValueError("scan-exception-gate")
    for finding in summary.get("findings", []):
        if (finding.get("severity") == "CRITICAL"
                and finding.get("decision") != "excepted"):
            raise ValueError("scan-exception-unapproved-critical")


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: image-scan-exception.py RECEIPT SUMMARY")
    receipt = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    summary = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    try:
        validate(receipt, summary)
    except ValueError as error:
        print("scan exception binding rejected: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
