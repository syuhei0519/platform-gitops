#!/usr/bin/env python3
"""Exercise the real image-mode gate with a sanitized fixed-layout report."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import importlib.util


CONFIG = "sha256:" + "a" * 64

exception_spec = importlib.util.spec_from_file_location(
    "image_scan_exception", Path("ci/image-scan-exception.py"))
exception_module = importlib.util.module_from_spec(exception_spec)
exception_spec.loader.exec_module(exception_module)


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
    write(root / "report.json", {
        "SchemaVersion": 2,
        "Trivy": {"Version": "0.75.0"},
        "ArtifactType": "container_image",
        "ArtifactName": ".oci/layout",
        "Metadata": {"ImageID": exception_module.POSTGRES["configDigest"]},
        "Results": [{
            "Target": "usr/local/bin/gosu",
            "Class": "lang-pkgs",
            "Type": "gobinary",
            "Vulnerabilities": [{
                "VulnerabilityID": "CVE-2025-68121",
                "PkgName": "stdlib",
                "InstalledVersion": "v1.24.6",
                "FixedVersion": "1.24.13, 1.25.7, 1.26.0-rc.3",
                "Severity": "CRITICAL",
            }],
        }],
    })
    command[-1] = exception_module.POSTGRES["configDigest"]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    summary = json.loads((root / "summary.json").read_text())
    exception_module.validate(dict(exception_module.POSTGRES), summary)
    wrong = dict(exception_module.POSTGRES)
    wrong["configDigest"] = "sha256:" + "b" * 64
    try:
        exception_module.validate(wrong, summary)
    except ValueError as error:
        if str(error) != "scan-exception-image-identity":
            raise
    else:
        raise SystemExit("scan exception passed a different OCI identity")
