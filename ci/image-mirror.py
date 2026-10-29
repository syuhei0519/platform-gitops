#!/usr/bin/env python3
"""Copy one inventoried OCI object once; fail closed on unknown registry state."""
import json
import os
from pathlib import Path
import subprocess
import sys

class MirrorError(ValueError): pass

def run(args, secret=None):
    return subprocess.run(args, input=secret, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False)

def state(result, expected):
    if result.returncode == 0:
        if result.stdout.strip() != expected: raise MirrorError("existing tag has different digest")
        return "same"
    error = result.stderr
    if "MANIFEST_UNKNOWN" in error and not any(x in error.lower() for x in ("unauthorized", "denied", "forbidden")):
        return "absent"
    raise MirrorError("tag state unavailable")

def mirror(name, source, destination, expected, output):
    login = run(["crane", "auth", "login", "ghcr.io", "-u", os.environ["GITHUB_ACTOR"], "--password-stdin"], os.environ["GH_TOKEN"])
    if login.returncode: raise MirrorError("authentication rejected")
    source_state = run(["crane", "digest", source])
    if source_state.returncode or source_state.stdout.strip() != expected: raise MirrorError("source identity unavailable")
    before = state(run(["crane", "digest", destination]), expected)
    if before == "absent":
        if state(run(["crane", "digest", destination]), expected) != "absent": raise MirrorError("tag changed before write")
        # One invocation; oci-copy-once disables go-containerregistry retries.
        write = run(["oci-copy-once", source, destination, expected])
        if write.returncode: raise MirrorError("write failed or outcome uncertain")
    if state(run(["crane", "digest", destination]), expected) != "same": raise MirrorError("remote verification failed")
    manifest = run(["crane", "manifest", destination])
    if manifest.returncode: raise MirrorError("manifest verification unavailable")
    document = json.loads(manifest.stdout)
    receipt = {"schemaVersion":"platform.lab/image-copy-receipt/v1", "name":name,
        "source":source, "destination":destination+"@"+expected, "manifestDigest":expected,
        "mediaType":document.get("mediaType"),
        "childManifestDigests":[item.get("digest") for item in document.get("manifests",[])]}
    output.mkdir(parents=True, exist_ok=True)
    (output/"manifest.json").write_text(manifest.stdout)
    (output/"receipt.json").write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":"))+"\n")

if __name__ == "__main__":
    try: mirror(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], Path(sys.argv[5]))
    except Exception as error: raise SystemExit("mirror stopped; registry state requires inspection: " + str(error))
