#!/usr/bin/env python3
"""Publish the public inspection receipt through GitHub-native metadata."""
import json
from pathlib import Path
import sys


FIELDS = {
    "schemaVersion", "name", "source", "topDigest", "topMediaType",
    "platform", "selectedManifestDigest", "selectedMediaType", "configDigest",
}


def publish(receipt_path, summary_path):
    receipt = json.loads(Path(receipt_path).read_text())
    if set(receipt) != FIELDS:
        raise ValueError("inspection receipt fields")
    if receipt["schemaVersion"] != "platform.lab/image-inspection-receipt/v1":
        raise ValueError("inspection receipt schema")
    compact = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    escaped = compact.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print("::notice title=OCI inspection receipt::" + escaped)
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write("### OCI inspection receipt\n\n```json\n" + compact + "\n```\n")


if __name__ == "__main__":
    try:
        publish(sys.argv[1], sys.argv[2])
    except Exception as error:
        raise SystemExit("inspection receipt output stopped: " + str(error))
