# Fresh profile lock (PLAT-213)

This directory is an offline design and validation boundary for one lightweight,
empty-data Account profile. It is not an installation procedure and grants no
authority to create a cluster, retrieve/publish an image, issue a credential, or
apply RBAC/admission/network changes.

The current `kind-learning-20261018-69c763` environment, its DB/PVC/CA/Sonar,
the maintained Core UI, and every historical PLAT-170 session are explicitly
outside this profile. The legacy `platform-lab` topology is not a reconstruction
source. `learning/existing-kind/current-contract.json` is comparison input only.

## Locked inputs

`profile.lock.yaml` records three external repository commits as 40-character
SHAs. The platform repository uses the explicit self-reference token
`__PLATFORM_GITOPS_SHA__`; `materialize.py` seals it to the final commit outside
Git and re-hashes the pending acceptance record before any approval request.
the tool/controller versions, every image required by this profile, the target
architecture, external input references, measurement limits and stop
conditions. Secret references contain metadata only; values and private key
material are forbidden by the validator.

The checked-in lock intentionally leaves these action-time decisions pending:

- **G1**: the proposed exact cluster/context are locked, while host thresholds,
  run window, sandbox retention and cleanup authority remain pending;
- **G2**: private registry or retained immutable OCI archive, owner, retention
  horizon, restore procedure and actual `linux/amd64` retrieval proof;
- **G3**: credential owner, initial distribution/recovery source and expiry;
- **G4**: cutover remains pending and is not required for isolated acceptance.

Pending values are valid for offline review. They fail `--require-approved`, so
this file cannot be mistaken for permission to run PLAT-217. A node cache or
expiring CI artifact is never supply evidence.

`image-supply.lock.yaml` makes that boundary executable. The Account runtime is
fixed to backend source `527b9bcc...`, manifest `746e9acb...`, entrypoint
`/server`, and user `10001:10001`. Its CI artifact metadata was checked, but it
is neither published nor retained for a cacheless fresh build. The remaining
images stay `pending-g2` until their real retrieval evidence is recorded.

An approved lock selects exactly one finite supply mode: an authenticated private
immutable registry reference, or an immutable retained OCI archive with its own
archive hash. For every image it records cache-empty retrieval, the observed
digest and architecture, entrypoint, user, args, migration/smoke,
scan and SBOM evidence. A multi-platform image binds both the index digest and
the selected child digest to child-specific scan/SBOM references. Retrieval
classifies `registry-unknown` separately from `image-absent`; neither is treated
as permission to reuse cache or retry blindly.

## Bootstrap and resume contract

`bootstrap-dag.yaml` fixes the dependency order:

`preflight → host image-source provenance → cluster → empty node cache →
image import → CRD Established → controller/webhook Ready → security →
credentials → custom resources → DB → role bootstrap → migration →
application → acceptance`.

Every completed step writes a journal outside Git with the profile, sealed
materialization, source set, live-adapter input-set digest and cluster UID.
Resume verifies those identities and completed resources, then
retries only the failed/incomplete step. A succeeded migration Job is observed,
not recreated. A rerun must be an idempotent verification of the same lock.

The authoritative checked-in DAG has fifteen steps, including separate
pre-cluster image provenance and post-cluster empty-cache/import proof. This
list supersedes the shorter historical sequence above.

The declared Account and minimal observability requests are reproducibly
measured with `measure_requests.py`. Control-plane overhead, archive bytes, and
all stop thresholds remain pending rather than guessed. The budget is measured
before, at peak and after. Crossing an approved memory, disk,
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
python learning/fresh-profile/validate_profile.py --require-approved
python learning/fresh-profile/validate_image_supply.py --require-fresh-evidence
```

The offline commands shown here must pass in CI. The `--require-*` commands are
expected to reject the checked-in pending profile until the user separately
approves G1-G3 and the lock is updated by review.

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

`acceptance.pending.json` and `acceptance.schema.json` define the evidence that
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
python learning/fresh-profile/validate_acceptance.py --require-complete
```

The first two commands are offline CI checks. The final command is expected to
fail until G1-G3 are approved, the lock is updated, and real observations are
recorded. It performs no cluster, image, Secret, fault or cleanup operation.
