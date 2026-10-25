#!/usr/bin/env python3
"""Seal external bootstrap inputs and prove Git-fetched sources are immutable."""

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
    "fixtures/security-negative-fixtures.json",
    "root/bootstrap.yaml",
    "root/children/applications.yaml",
    "security/canonical-contract.json",
    "security/rbac.yaml",
    "security/admission.yaml",
)
GIT_FETCHED_PLATFORM_FILES = (
    "root/children/applications.yaml",
    "observability/resources.yaml",
)
APPLICATION_RELEASE_FILES = (
    "Chart.yaml",
    "values.yaml",
    "templates/application.yaml",
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
    release_root = Path(application_release)
    if not release_root.is_dir() or any(not (release_root / relative).is_file()
                                        for relative in APPLICATION_RELEASE_FILES):
        raise MaterializationError("application-release-chart")
    profile_text = (HERE / "profile.lock.yaml").read_text(encoding="utf-8")
    children_text = (HERE / "root/children/applications.yaml").read_text(encoding="utf-8")
    if application_sha not in profile_text or application_sha not in children_text:
        raise MaterializationError("application-sha-not-locked")
    for relative in GIT_FETCHED_PLATFORM_FILES:
        text = (HERE / relative).read_text(encoding="utf-8")
        if PLATFORM_TOKEN in text or APPLICATION_TOKEN in text:
            raise MaterializationError("git-source-revision-token:" + relative)
    for relative in APPLICATION_RELEASE_FILES:
        text = (release_root / relative).read_text(encoding="utf-8")
        if PLATFORM_TOKEN in text or APPLICATION_TOKEN in text:
            raise MaterializationError("application-source-revision-token:" + relative)

    output.mkdir(parents=True, exist_ok=False)
    produced = []
    for relative in PLATFORM_FILES:
        source = HERE / relative
        text = source.read_text(encoding="utf-8")
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if PLATFORM_TOKEN in text:
            target.write_text(text.replace(PLATFORM_TOKEN, platform_sha), encoding="utf-8")
        else:
            target.write_bytes(source.read_bytes())
        produced.append(target)

    sealed_profile = output / "profile.lock.yaml"
    sealed_acceptance = output / "acceptance.pending.json"
    acceptance = json.loads(sealed_acceptance.read_text(encoding="utf-8"))
    acceptance["profile"]["sha256"] = digest(sealed_profile)
    sealed_acceptance.write_text(json.dumps(acceptance, indent=2) + "\n", encoding="utf-8")

    if any(PLATFORM_TOKEN in path.read_text(encoding="utf-8") or
           APPLICATION_TOKEN in path.read_text(encoding="utf-8") for path in produced):
        raise MaterializationError("unresolved-revision-token")
    sealed_children = output / "root/children/applications.yaml"
    if sealed_children.read_bytes() != (HERE / "root/children/applications.yaml").read_bytes():
        raise MaterializationError("git-source-sealed-drift")
    source_files = {
        f"platform-gitops@{platform_sha}:{relative}": digest(HERE / relative)
        for relative in GIT_FETCHED_PLATFORM_FILES
    }
    source_files.update({
        f"application-manifest@{application_sha}:learning/fresh-profile/release-definition/{relative}":
            digest(release_root / relative)
        for relative in APPLICATION_RELEASE_FILES
    })
    manifest = {
        "schemaVersion": "platform.lab/materialization/v1alpha1",
        "platformGitops": platform_sha,
        "applicationManifest": application_sha,
        "liveApproved": False,
        "files": {str(path.relative_to(output)).replace("\\", "/"): digest(path)
                  for path in sorted(produced)},
        "sourceFiles": source_files,
    }
    manifest_path = output / "materialization.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-sha", required=True)
    parser.add_argument("--application-sha", required=True)
    parser.add_argument("--application-release", type=Path, required=True,
                        help="Path to the immutable application release-definition chart")
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
