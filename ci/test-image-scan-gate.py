#!/usr/bin/env python3
"""Exercise the real image-mode gate with a sanitized fixed-layout report."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile


CONFIG = "sha256:" + "a" * 64


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    write(root / "db.json", {
        "Version": 2,
        "UpdatedAt": datetime.now(timezone.utc).isoformat(),
    })
    write(root / "report.json", {
        "SchemaVersion": 2,
        "Trivy": {"Version": "0.75.0"},
        "ArtifactType": "container_image",
        "ArtifactName": ".oci/layout",
        "Metadata": {"ImageID": CONFIG},
        "Results": [{
            "Target": ".oci/layout (fixture 1.0)",
            "Class": "os-pkgs",
            "Type": "fixture",
            "Packages": [{"Name": "fixture", "Version": "1.0"}],
        }],
    })
    command = [
        ".security/scan-gate", "-policy", "security/scan-policy.json",
        "-db", str(root / "db.json"), "-report", str(root / "report.json"),
        "-out", str(root / "summary.json"), "-scanner-exit", "0",
        "-image-config", CONFIG,
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    mismatch = command[:-1] + ["sha256:" + "b" * 64]
    if subprocess.run(mismatch, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("mismatched image config passed the real scan gate")
