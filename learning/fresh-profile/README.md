# Fresh profile lock (PLAT-213)

This directory is an offline design and validation boundary for one lightweight,
empty-data Account profile. It is not an installation procedure and grants no
authority to create a cluster, retrieve/publish an image, issue a credential, or
apply RBAC/admission/network changes.

The runtime-declared protected environment, its DB/PVC/CA/Sonar,
the maintained Core UI, and every historical PLAT-170 session are explicitly
outside this profile. The legacy `platform-lab` topology is not a reconstruction
source. `learning/existing-kind/current-contract.json` is comparison input only.

## Public contract and private runtime evidence

`bootstrap.contract.yaml` is the public, reproducible contract. It contains the
official Rollouts chart/app versions, package and five-CRD List hashes, and
general fail-closed safety limits. `profile.template.yaml`,
`image-supply.template.yaml`, `acceptance.template.json`, and
`approval.template.json` contain placeholders only for environment-specific
inputs.

Before an approved run, an operator copies the templates into
`learning/fresh-profile/.runtime/` and records the target cluster/context, host
and Docker observations, immutable image supply evidence, credential-reference
metadata, approval and acceptance record there. That directory and every
`*.runtime.local.*` file are Git-ignored. Secret values and private key material
remain forbidden. `materialize.py` accepts those runtime files explicitly,
refuses tracked files as runtime evidence, seals copies outside Git, and binds
their hashes before any executor can run.

The platform repository uses the explicit self-reference token
`__PLATFORM_GITOPS_SHA__`; materialization replaces it only in the sealed
runtime copy.

The checked-in templates intentionally leave these action-time decisions pending:

- **G1**: cluster/context and current-host observations exist only in runtime
  evidence; public start/stop limits remain fixed by the public contract;
- **G2**: private registry or retained immutable OCI archive, owner, retention
  horizon, restore procedure and actual `linux/amd64` retrieval proof;
- **G3-P**: the non-secret exact issuance plan (Secret target/type/key names,
  owner, expiry, issuer, cleanup owner and a procedure reference/hash) and the
  authority to use that plan. The current validator checks the hash's format
  and seals its value; it does not read or hash the referenced runbook bytes;
- **G3-R**: the later issuance receipt plus fresh Secret/CA metadata and UIDs,
  recorded at `credential-bootstrap` before `custom-resources`;
- **G4**: cutover remains pending and is not required for isolated acceptance.

Pending values are valid for offline review. They fail `--require-approved`, so
the templates cannot be mistaken for permission to run PLAT-217. A node cache or
expiring CI artifact is never supply evidence.

`image-supply.template.yaml` keeps every source at `pending-g2`. Source/archive
locations, CI identifiers, retention ownership and observed digests belong only
in the ignored runtime evidence. The validator still binds runtime evidence to
the profile's immutable image identities before it can unlock a run.

An approved lock selects exactly one finite supply mode: an authenticated private
immutable registry reference, or an immutable retained OCI archive with its own
archive hash. For every image it records cache-empty retrieval, the observed
digest and architecture, entrypoint, user, args, migration/smoke,
scan and SBOM evidence. `smoke` is a selected-architecture isolated image
check, not Kubernetes integration. `migration` is the Account application
image's schema-migration capability: only `account-runtime` is required; the
other ten fixed roles are `not-applicable` because they do not own it.
PostgreSQL is the server, not the migration client. Its compatibility,
initialization, restart/PVC behavior and the actual migration result remain
mandatory later DAG evidence. A multi-platform image binds both the index digest and
the selected child digest to child-specific scan/SBOM references. Retrieval
classifies `registry-unknown` separately from `image-absent`; neither is treated
as permission to reuse cache or retry blindly.

Check status is raw evidence. `failed` or `unrun` is never rewritten to
`passed`. A bounded lab scan disposition is separate external evidence and
cannot satisfy native full G2 by changing the raw scan field.

## Bootstrap and resume contract

`bootstrap-dag.yaml` fixes the dependency order:

`preflight → host image-source provenance → cluster → empty node cache →
image import → CRD Established → controller/webhook Ready → security →
credentials → custom resources → DB → role bootstrap → migration →
application → acceptance`.

Every completed step writes a journal outside Git with the runtime profile, sealed
materialization, source set, live-adapter input-set digest and cluster UID.
Resume verifies those identities and completed resources, then
retries only the failed/incomplete step. A succeeded migration Job is observed,
not recreated. A rerun must be an idempotent verification of the same lock.

The authoritative checked-in DAG has fifteen steps, including separate
pre-cluster image provenance and post-cluster empty-cache/import proof. This
list supersedes the shorter historical sequence above.

An approval may authorize only the fixed prefix ending at `controllers`, or
the complete fifteen-step DAG. The prefix still requires exact target,
G1/G2/G3-P/security approval and every sealed hash; it only avoids requiring
not-yet-created G3-R. A later full resume requires a new unexpired approval and
typed G3-R before any dependent step. The current adapter has no safe
metadata-only Secret/CA receipt transport, so the full path remains fail-closed
at that point. A Deployment observation is not credential proof. The prefix is
partial evidence, not full G2 or PLAT-217 completion.

This change defines that prefix contract for offline review; it does not make
the current runtime inputs live-ready. The same native G2 validator still
requires every applicable raw source check to be `passed` for both the prefix
and the full DAG. It does not consume an external scan-exception disposition.
Consequently any current raw scan `failed` or source check `unrun` stops the
prefix before executor identity or mutation. A separately supervised manual
experiment is not execution of this runner prefix and must not be recorded as
native G2 or PLAT-217 completion.

This is a fail-closed contract migration within `v1alpha1`: runtime profile,
image-supply and approval files made from the earlier templates must be
regenerated and re-approved. They must not be edited under or used to resume an
existing runner journal because the sealed profile/input digests differ.

Argo Rollouts uses exactly one official `argo-rollouts` chart (`2.43.7`, app
`v1.10.1`). `render_rollouts_bootstrap.py` verifies the retained chart archive
size and SHA-256, renders only the five pinned CRDs to a deterministic JSON
`List`, and proves a second render with `installCRDs=false` contains no CRD.
The `crds` step applies that hash-bound list and requires every CRD to report
`Established=True`; the `controllers` step installs exactly one
`argo-rollouts` Helm release with `installCRDs=false`. There is no separate
`argo-rollouts-crds` chart or release. The canonical CRD List SHA-256 is
`9c4794e462b522f45077f0dabaf39df1ba3a35d29ec28787f6007ea989ddf07b`;
neither the plan's content hash nor per-resource desired hashes can replace it.
During resume, CRD reads use `--ignore-not-found=true`: only an absent CRD is
an incomplete step, while authorization, context and connectivity failures
remain fatal.

The declared Account and minimal observability requests are reproducibly
measured with `measure_requests.py`. Host observations, control-plane overhead
and archive bytes remain runtime evidence rather than public machine data. The
budget is measured before, at peak and after. Crossing the public memory, disk,
elapsed, Pending or unexpected-restart limit stops the batch; it does not
silently enlarge the host or omit a component.

## Prior evidence

`equivalence.json` maps PLAT-177 and PLAT-193–199 to the checks that must be
repeated. Previous evidence is a comparison oracle only. Fresh resource/field
ownership, API, observability, Secret, DB, resync, rerun/resume and sandbox-only
cleanup evidence remains required.

## Offline validation

```text
python learning/fresh-profile/validate_profile.py
python learning/fresh-profile/test_validate_profile.py
python learning/fresh-profile/validate_image_supply.py
python learning/fresh-profile/test_validate_image_supply.py
python learning/fresh-profile/test_validate_manifests.py
python learning/fresh-profile/test_measure_requests.py
python learning/fresh-profile/test_materialize.py
python learning/fresh-profile/test_render_rollouts_bootstrap.py
python learning/fresh-profile/test_runtime_evidence_separation.py
python learning/fresh-profile/validate_profile.py --profile <ignored-runtime-profile> --require-approved
python learning/fresh-profile/validate_image_supply.py --lock <ignored-runtime-supply> --require-fresh-evidence
```

Given an already retained official chart and Helm executable, the offline
render is:

```text
python learning/fresh-profile/render_rollouts_bootstrap.py --helm <helm> --chart <argo-rollouts-2.43.7.tgz> --output <rollouts-crds.json>
```

The expected chart is 68,002 bytes with SHA-256
`166506174f4be91ed2758fccbca340e209cba35aa72ac08b5863a7b797942487`.
The renderer performs no cluster operation.

The offline commands shown here must pass in CI. The `--require-*` commands are
expected to reject the checked-in templates until the user separately approves
G1, G2 and G3-P and ignored runtime evidence is sealed by review.

## Authority proposal (PLAT-215)

`authority-matrix.json` separates platform, workload and release-definition
sources by materialized repository SHA, destination, kind, verb and resource
name. The matrix separately names the actual Argo controller KSA and workload
KSAs; Git source labels are not treated as Kubernetes subjects. The exact
Role/RoleBinding and ValidatingAdmissionPolicy/Binding designs are under
`security/`.
`fixtures/canonical-two-sha.json` is an offline positive fixture; it is not a
record of an applied policy or a successful sync. The validator also enforces
the reviewed ServiceAccount, disabled token automount, finite Secret keys,
PVC/Job retention and field-owner split for controller-created Pods.

```text
python learning/fresh-profile/validate_authority.py
python learning/fresh-profile/test_validate_authority.py
```

The checked-in matrix deliberately reports live evidence as `not-run`. Actual
AppProject/RBAC/admission changes, `kubectl auth can-i`, admission denials and
canonical two-SHA sync need separate action-time approval on the isolated
fresh cluster. The offline fixture must never be reported as live effectiveness.

## PLAT-217 acceptance record

`acceptance.template.json` and `acceptance.schema.json` define the evidence that
must be produced by a separately approved live run. The checked-in record is
deliberately `liveEvidence: false`: every observation, negative case, first
run, idempotent rerun and interrupted-resume result is `unrun`. The offline
validator rejects any result mixed into that pending record, so unit tests and
rendering cannot be reported as fresh-cluster acceptance.

The record locks the profile hash, all four repository revisions, every image
digest and architecture, a nonempty expected resource set, field owners,
readiness/API/metrics/traces, telemetry profile/Pod UID, actual DB role owner,
resync/restart, DB-down recovery, old-image rollback, and six fail-closed
negative cases. `live-acceptance-plan.md` fixes the fault targets and recovery
requirements; the prior Account rollback digest remains an explicit PLAT-211
input. It also fixes cleanup to `new-sandbox-only` and requires
separate evidence that the current cluster and existing data were unchanged.

```text
python learning/fresh-profile/validate_acceptance.py
python learning/fresh-profile/test_validate_acceptance.py
python learning/fresh-profile/validate_acceptance.py --record <ignored-runtime-acceptance> --profile <ignored-runtime-profile> --require-complete
```

The first two commands are offline CI checks. The final command is expected to
fail until G1, G2 and G3-P are approved, G3-R exists at its runtime step, and
real observations are recorded only in the
ignored runtime evidence. It performs no cluster, image, Secret, fault or
cleanup operation.
