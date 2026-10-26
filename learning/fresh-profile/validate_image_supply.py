#!/usr/bin/env python3
"""Validate PLAT-212 image supply evidence without retrieving an image."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re

import yaml
from jsonschema import Draft7Validator


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
LOCK = HERE / "image-supply.template.yaml"
SCHEMA = HERE / "image-supply.schema.json"
REQUIRED_PROHIBITED = {"old-node-cache", "ci-artifact-only", "tag-without-digest"}
REQUIRED_CHECKS = {
    "digest", "architecture", "entrypoint", "user", "args",
    "migration", "smoke", "scan", "sbom",
}
MIGRATION_REQUIRED_IMAGE = "account-runtime"
MIGRATION_REQUIRED_REASON = "account-runtime-owns-account-schema-migration"
MIGRATION_NOT_APPLICABLE = {
    "applicability": "not-applicable",
    "rawResult": "not-applicable",
    "evidenceRef": "N-A",
    "evidenceScope": "not-applicable",
    "reason": "image-does-not-own-account-schema-migration",
}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class SupplyError(ValueError):
    pass


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def profile_path(lock) -> Path:
    candidate = (REPOSITORY / lock["spec"]["profileRef"]).resolve()
    if not candidate.is_relative_to(REPOSITORY.resolve()) or not candidate.is_file():
        raise SupplyError("profile-ref")
    return candidate


def image_digest(reference: str) -> str:
    match = re.search(r"@(?P<digest>sha256:[0-9a-f]{64})$", reference)
    if not match:
        raise SupplyError("profile-image-digest")
    return match.group("digest")


def platform_parts(value: str) -> tuple[str, str]:
    parts = value.split("/", 1)
    if len(parts) != 2:
        raise SupplyError("target-architecture")
    return parts[0], parts[1]


def validate(lock_path: Path = LOCK, require_fresh_evidence: bool = False,
             profile_override: Path | None = None,
             require_source_evidence: bool = False):
    lock = load_yaml(lock_path)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(Draft7Validator(schema).iter_errors(lock), key=lambda error: list(error.path))
    if errors:
        raise SupplyError("schema:" + "/".join(map(str, errors[0].path)))

    spec = lock["spec"]
    if set(spec["prohibitedEvidence"]) != REQUIRED_PROHIBITED:
        raise SupplyError("prohibited-evidence")
    if set(spec["requiredChecks"]) != REQUIRED_CHECKS:
        raise SupplyError("required-checks")

    profile = load_yaml(profile_override or profile_path(lock))
    profile_spec = profile["spec"]
    if spec["targetArchitecture"] != profile_spec["target"]["architecture"]:
        raise SupplyError("profile-architecture")
    profile_images = {item["name"]: item for item in profile_spec["images"]}
    supplied = spec["images"]
    if len(supplied) != len({item["name"] for item in supplied}):
        raise SupplyError("duplicate-image")
    if {item["name"] for item in supplied} != set(profile_images):
        raise SupplyError("image-set")

    decision = spec["decision"]
    pending = decision["status"] == "pending-approval"
    if pending:
        if decision["selectedMode"] != "PENDING_G2":
            raise SupplyError("pending-mode")
    else:
        if decision["selectedMode"] not in {"private-registry", "retained-oci-archive",
                                             "public-upstream-plus-retained-account-archive"}:
            raise SupplyError("approved-mode")
        for field in ("restoreOwner", "retentionUntil", "restoreProcedureRef"):
            if decision[field].startswith("PENDING_"):
                raise SupplyError("approved-placeholder")
        try:
            retention_until = date.fromisoformat(decision["retentionUntil"])
        except ValueError as error:
            raise SupplyError("retention-date") from error
        if retention_until <= date.today():
            raise SupplyError("retention-expired")

    profile_g2 = profile_spec["decisions"]["G2"]
    profile_supply = profile_spec["imageSupply"]
    if pending:
        if profile_g2["status"] != "pending-approval" or profile_supply["decision"] != "PENDING_G2":
            raise SupplyError("profile-g2-pending")
    elif (profile_g2 != {"status": "approved", "choice": decision["selectedMode"]}
          or profile_supply["decision"] != decision["selectedMode"]
          or not profile_supply["immutable"]
          or profile_supply["retentionUntil"] != decision["retentionUntil"]
          or profile_supply["restoreOwner"] != decision["restoreOwner"]):
        raise SupplyError("profile-g2-approved")

    os_name, architecture = platform_parts(spec["targetArchitecture"])
    for item in supplied:
        name = item["name"]
        profile_ref = profile_images[name]["ref"]
        if profile_ref == "PENDING_PLAT211_VERIFIED_IMAGE":
            if name != "account-runtime" or not pending or item["source"]["kind"] != "pending-g2":
                raise SupplyError("account-runtime-pending")
            expected = None
        else:
            expected = image_digest(profile_ref)
        source = item["source"]
        retrieval = item["retrieval"]
        checks = item["checks"]

        migration = checks["migration"]
        if name == MIGRATION_REQUIRED_IMAGE:
            if (migration.get("applicability") != "required"
                    or migration.get("reason") != MIGRATION_REQUIRED_REASON):
                raise SupplyError("migration-required:" + name)
        elif migration != MIGRATION_NOT_APPLICABLE:
            # PLAT-212's migration check means the application image's schema
            # migration execution capability.  PostgreSQL is the server and
            # the remaining platform images do not own Account migrations.
            raise SupplyError("migration-not-applicable:" + name)

        comparison = item.get("comparisonOnly")
        if comparison and (comparison["source"] != "old-node-cache" or comparison["architecture"] != architecture):
            raise SupplyError("comparison-boundary:" + name)
        if name == "account-runtime" and source["kind"] == "checked-local-artifact":
            if source["manifestDigest"] != expected:
                raise SupplyError("account-artifact-profile-digest")

        if source["kind"] == "pending-g2" and not pending:
            raise SupplyError("approved-pending-source:" + name)
        if not pending:
            if decision["selectedMode"] == "public-upstream-plus-retained-account-archive":
                expected_kind = "retained-oci-archive" if name == "account-runtime" else "public-registry"
            else:
                expected_kind = decision["selectedMode"]
            if source["kind"] != expected_kind:
                raise SupplyError("approved-source-mode:" + name)
        if source["kind"] == "checked-local-artifact" and source["freshEligible"]:
            raise SupplyError("checked-artifact-eligible:" + name)
        if source["kind"] in {"private-registry", "public-registry", "retained-oci-archive"}:
            if (decision["selectedMode"] != "public-upstream-plus-retained-account-archive"
                    and source["kind"] != decision["selectedMode"]):
                raise SupplyError("mixed-approved-mode:" + name)
            manifest = source["manifest"]
            if manifest["os"] != os_name or manifest["architecture"] != architecture:
                raise SupplyError("manifest-architecture:" + name)
            if manifest["kind"] == "single-platform":
                if manifest["manifestDigest"] != expected:
                    raise SupplyError("manifest-digest:" + name)
                retrieval_digest = manifest["manifestDigest"]
            else:
                if manifest["indexDigest"] != expected or manifest["indexDigest"] == manifest["childDigest"]:
                    raise SupplyError("index-child-digest:" + name)
                if manifest["childScanRef"] == manifest["childSbomRef"]:
                    raise SupplyError("index-child-evidence:" + name)
                retrieval_digest = manifest["childDigest"]
            if source["kind"] in {"private-registry", "public-registry"}:
                location_digest = DIGEST.search(source["location"])
                if location_digest is None or location_digest.group(0) != expected:
                    raise SupplyError("registry-digest:" + name)
        else:
            retrieval_digest = expected

        if require_source_evidence or require_fresh_evidence:
            if pending:
                raise SupplyError("approval-g2")
            if expected is None:
                raise SupplyError("account-runtime-pending")
            if not source["freshEligible"]:
                raise SupplyError("ineligible-source:" + name)
            for check_name in REQUIRED_CHECKS:
                check = checks[check_name]
                if check_name == "migration" and check["applicability"] == "not-applicable":
                    continue
                raw_result = check.get("rawResult", check.get("status"))
                if raw_result != "passed" or check["evidenceRef"].startswith("PENDING_"):
                    raise SupplyError(f"check-{check_name}:{name}")
        if require_fresh_evidence:
            status = retrieval["status"]
            if status == "registry-unknown":
                raise SupplyError("registry-unknown:" + name)
            if status == "image-absent":
                raise SupplyError("image-absent:" + name)
            if status != "succeeded" or not retrieval["emptyNodeCache"]:
                raise SupplyError("fresh-retrieval:" + name)
            if retrieval["observedDigest"] != retrieval_digest:
                raise SupplyError("observed-digest:" + name)
            if retrieval["observedArchitecture"] != spec["targetArchitecture"]:
                raise SupplyError("observed-architecture:" + name)

    return lock, profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--require-fresh-evidence", action="store_true")
    parser.add_argument("--require-source-evidence", action="store_true")
    args = parser.parse_args()
    try:
        lock, profile = validate(args.lock, args.require_fresh_evidence,
                                 require_source_evidence=args.require_source_evidence)
    except (SupplyError, yaml.YAMLError, json.JSONDecodeError) as error:
        print(f"image supply rejected: {error}")
        return 1
    print("image supply valid",
          f"images={len(lock['spec']['images'])}",
          f"profile={profile['metadata']['name']}",
          f"mode={lock['spec']['decision']['selectedMode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
