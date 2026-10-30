#!/usr/bin/env python3
"""Read and record one fixed upstream OCI identity without registry writes."""
import json
from pathlib import Path
import re
import subprocess
import sys


class InspectError(ValueError):
    pass


def run(args):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False)


def failure_category(stderr):
    """Return a bounded public classification without echoing registry output."""
    diagnostic = re.sub(r"(?i)https?://\S+", "", stderr)
    status = re.search(
        r"(?i)\b(?:http(?:/\d(?:\.\d)?)?\s+|"
        r"status(?:\s+code)?\s*(?:[:=]\s*)?)(401|403|404|429)\b",
        diagnostic)
    if status:
        return "http-" + status.group(1)
    if re.search(r"(?i)\b(?:timed?\s*out|timeout|deadline exceeded)\b", diagnostic):
        return "timeout"
    if re.search(
            r"(?i)\b(?:unsupported (?:media|manifest)(?: type)?|"
            r"(?:media|manifest) type [^\r\n]{0,80} not supported)\b",
            diagnostic):
        return "unsupported-media"
    return "unknown"


def checked(args, message):
    result = run(args)
    if result.returncode:
        raise InspectError(
            f"{message} (crane_exit={result.returncode}; "
            f"category={failure_category(result.stderr)})")
    return result.stdout


def inspect(name, source, expected, output):
    top_digest = checked(["crane", "digest", source], "source identity unavailable").strip()
    if top_digest != expected:
        raise InspectError("source digest mismatch")

    top_raw = checked(["crane", "manifest", source], "top manifest unavailable")
    top = json.loads(top_raw)
    descriptors = top.get("manifests")
    if descriptors is None:
        selected_digest = expected
        selected = top
        selected_raw = top_raw
        selected_reference = source
    else:
        candidates = [item for item in descriptors
                      if item.get("platform", {}).get("os") == "linux" and
                      item.get("platform", {}).get("architecture") == "amd64"]
        if len(candidates) != 1:
            raise InspectError("linux/amd64 child is not unique")
        selected_digest = candidates[0].get("digest")
        child_reference = source.rpartition("@")[0] + "@" + selected_digest
        actual_child = checked(
            ["crane", "digest", child_reference],
            "child identity unavailable").strip()
        if actual_child != selected_digest:
            raise InspectError("child digest mismatch")
        selected_raw = checked(
            ["crane", "manifest", child_reference],
            "child manifest unavailable")
        selected = json.loads(selected_raw)
        if candidates[0].get("mediaType") != selected.get("mediaType"):
            raise InspectError("child media type mismatch")
        selected_reference = child_reference

    config_digest = selected.get("config", {}).get("digest")
    if not isinstance(config_digest, str) or not config_digest.startswith("sha256:"):
        raise InspectError("config digest unavailable")
    config_raw = checked(["crane", "config", selected_reference],
                         "config unavailable")
    config = json.loads(config_raw)
    if config.get("os") != "linux" or config.get("architecture") != "amd64":
        raise InspectError("config platform mismatch")
    receipt = {
        "schemaVersion": "platform.lab/image-inspection-receipt/v1",
        "name": name,
        "source": source,
        "topDigest": expected,
        "topMediaType": top.get("mediaType"),
        "platform": "linux/amd64",
        "selectedManifestDigest": selected_digest,
        "selectedMediaType": selected.get("mediaType"),
        "configDigest": config_digest,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "top-manifest.json").write_text(top_raw)
    (output / "linux-amd64-manifest.json").write_text(selected_raw)
    (output / "linux-amd64-config.json").write_text(config_raw)
    (output / "receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    try:
        inspect(sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4]))
    except Exception as error:
        raise SystemExit("inspection stopped; source identity is unverified: " + str(error))
