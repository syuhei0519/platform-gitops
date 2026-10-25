#!/usr/bin/env python3
"""Seal self-referential fresh-profile inputs outside Git; no cluster I/O."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
PLATFORM_TOKEN = "__PLATFORM_GITOPS_SHA__"
APPLICATION_TOKEN = "__APPLICATION_MANIFEST_SHA__"
SHA = re.compile(r"[0-9a-f]{40}")
PLATFORM_FILES = (
    "profile.lock.yaml",
    "acceptance.pending.json",
    "authority-matrix.json",
    "fixtures/canonical-two-sha.json",
    "root/bootstrap.yaml",
    "root/children/applications.yaml",
)


class MaterializationError(ValueError):
    pass


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def materialize(platform_sha: str, application_sha: str, application_release: Path,
                output: Path):
    if not SHA.fullmatch(platform_sha) or not SHA.fullmatch(application_sha):
        raise MaterializationError("revision-must-be-40-hex")
    output = output.resolve()
    if output == REPOSITORY.resolve() or output.is_relative_to(REPOSITORY.resolve()):
        raise MaterializationError("output-must-be-outside-repository")
    release_text = Path(application_release).read_text(encoding="utf-8")
    if APPLICATION_TOKEN not in release_text:
        raise MaterializationError("application-release-token")
    if application_sha not in (HERE / "profile.lock.yaml").read_text(encoding="utf-8"):
        raise MaterializationError("application-sha-not-locked")

    output.mkdir(parents=True, exist_ok=False)
    produced = []
    for relative in PLATFORM_FILES:
        source = HERE / relative
        text = source.read_text(encoding="utf-8")
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.replace(PLATFORM_TOKEN, platform_sha), encoding="utf-8")
        produced.append(target)

    sealed_release = output / "application-manifest" / "fresh-account-release.yaml"
    sealed_release.parent.mkdir(parents=True, exist_ok=True)
    sealed_release.write_text(release_text.replace(APPLICATION_TOKEN, application_sha), encoding="utf-8")
    produced.append(sealed_release)

    sealed_profile = output / "profile.lock.yaml"
    sealed_acceptance = output / "acceptance.pending.json"
    acceptance = json.loads(sealed_acceptance.read_text(encoding="utf-8"))
    acceptance["profile"]["sha256"] = digest(sealed_profile)
    sealed_acceptance.write_text(json.dumps(acceptance, indent=2) + "\n", encoding="utf-8")

    if any(PLATFORM_TOKEN in path.read_text(encoding="utf-8") or
           APPLICATION_TOKEN in path.read_text(encoding="utf-8") for path in produced):
        raise MaterializationError("unresolved-revision-token")
    manifest = {
        "schemaVersion": "platform.lab/materialization/v1alpha1",
        "platformGitops": platform_sha,
        "applicationManifest": application_sha,
        "liveApproved": False,
        "files": {str(path.relative_to(output)).replace("\\", "/"): digest(path)
                  for path in sorted(produced)},
    }
    manifest_path = output / "materialization.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-sha", required=True)
    parser.add_argument("--application-sha", required=True)
    parser.add_argument("--application-release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        path, manifest = materialize(args.platform_sha, args.application_sha,
                                     args.application_release, args.output)
    except (MaterializationError, OSError, json.JSONDecodeError) as error:
        print(f"materialization rejected: {error}")
        return 1
    print("materialization sealed", path, f"files={len(manifest['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
