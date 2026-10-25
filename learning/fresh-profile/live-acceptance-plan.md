# PLAT-217 isolated live acceptance plan

Status: **offline design only; every live action below is unrun**. This file is
not an approval and must not be treated as evidence. Every command must use the
approved run-scoped kubeconfig and explicit context
`kind-learning-fresh-plat217-20261025`. The existing
`learning-20261018-69c763` cluster is never a target.

## Ordered gates

1. G1 approves creation and the measured stop thresholds for exactly
   `learning-fresh-plat217-20261025`. Record host/Docker before values and stop
   without starting a new step if any approved threshold is crossed.
2. G2 first fixes host/archive provenance without requiring a cluster. After
   cluster creation, record an empty fresh-node workload cache before import,
   then index digest, selected
   `linux/amd64` child digest, config digest, and source type after import.
3. Security approval covers only the AppProjects in `root/bootstrap.yaml`, the
   three namespaced Role/RoleBinding pairs in `security/rbac.yaml`, and the three
   ValidatingAdmissionPolicy/Binding pairs in `security/admission.yaml`.
4. G3 approves fresh-isolated credentials and fresh-sandbox CA creation. Record
   metadata/key names only. Existing Secret or CA read/export/transfer is a
   separate approval and is not part of this plan.
5. Execute the 15-step `bootstrap-dag.yaml`, recording the materialized profile
   digest, live-adapter input-set digest, cluster UID, resource UIDs,
   input-reference digests, status, and
   timestamps after every step.

## Positive evidence

- Applications: `Application/fresh-control/account-lightweight-root`,
  `fresh-release-definitions`, `fresh-account-release`, and
  `fresh-observability` are non-empty and use the two materialized repository
  SHAs.
- Database: `StatefulSet/fresh-account/account-postgresql` uses new
  `PersistentVolumeClaim/fresh-account/account-data`. Prove the database owner
  is `postgres`, schema `account` owner is `account_migrator`, and the API
  session user is `account_runtime`; do not output connection strings.
- Identity: both supported telemetry profile inputs are accepted. Prove the
  emitted resource carries `platform.profile=fresh-account` and the actual Pod
  UID derived through Downward API, not a static UID.
- First run, verification-only rerun, and one resume from an interrupted
  client-side checkpoint all complete. A succeeded migration Job is never
  recreated during rerun/resume.
- Existing cluster/container, existing DB/PVC/CA, Sonar, and Core UI identities
  and health remain unchanged before and after the run.

## Negative and recovery cases requiring their own approval

| Case | Exact new-sandbox target/action | Required recovery proof |
|---|---|---|
| Missing runtime Secret | Withhold `Secret/fresh-account/account-db-runtime` before the application step | Create the already-approved fresh Secret, resync `Application/fresh-control/fresh-account-release`, and prove Ready/authentication without reading its value |
| Wrong runtime Secret | Substitute a separately approved invalid fresh-only `Secret/fresh-account/account-db-runtime`; no existing credential is reused | Restore the approved fresh Secret reference, restart `Rollout/fresh-account/account-api`, and prove Ready plus old/invalid rejection |
| Wrong profile identity | Supply a non-canonical profile label/attribute fixture to the new collector path | Restore `fresh-account`, restart the Rollout, prove accepted profile plus actual Pod UID |
| DB down/recovery | Delete only `Pod/fresh-account/account-postgresql-0` after the role/migration evidence is sealed | StatefulSet recreates the Pod against `PVC/fresh-account/account-data`; prove schema owner, runtime role, API recovery, and persistent test row identity |
| Workload restart | Restart only `Rollout/fresh-account/account-api` | Same image digest, repository SHAs, DB role, profile, new Pod UID, Ready/API/metrics/traces |
| Old-image rollback | Apply a separately approved retained prior image **and its matching prior config** pair | Prove revision/role/profile/readiness after the paired restore. **Blocked until the prior pair has an exact source, digest and retained G2 source; `746e9acb...` is not assumed fresh-profile compatible.** |
| Healthy root / empty child | Use an offline/materialized empty-child fixture; do not mutate the existing cluster | Gate rejects the expected-resource mismatch, then canonical two-SHA input passes |
| Wrong architecture | Use the preflight fixture only, before image import or resource apply | Gate rejects; restore canonical lock. No live resource recovery is needed |

The DB Pod deletion and Secret substitution are destructive/fault operations
and remain paused until their exact approvals are recorded. No fault targets an
old-cluster Pod, PVC, Secret, CA, RBAC, CNI, or NetworkPolicy.

## Cleanup

Stop run-scoped port-forwards/log followers. Securely remove the run-scoped
kubeconfig and any temporary credential input file. Cluster deletion is a
separate cleanup approval targeting only
`learning-fresh-plat217-20261025`; its disposable empty-data PVC is recovered by
recreating the sandbox from the materialized locks and retained G2 archives,
not by reading the existing environment. Retain redacted journals and hashes
until the independent GPT-6 review disposition, then let the named G2 owner
delete the archives under the approved retention rule.
