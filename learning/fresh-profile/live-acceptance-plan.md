# PLAT-217 isolated live acceptance plan

Status: **offline design only; every live action below is unrun**. This file is
not an approval and must not be treated as evidence. Every command must use the
approved run-scoped kubeconfig and explicit context for the runtime-approved
fresh sandbox. The existing
runtime-declared protected cluster is never a target.

## Ordered gates

1. G1 approves creation and the measured stop thresholds for exactly
   runtime-approved cluster name. Record host/Docker before values and stop
   without starting a new step if any approved threshold is crossed.
2. G2 first fixes host/archive provenance without requiring a cluster. After
   cluster creation, record an empty fresh-node workload cache before import,
   then index digest, selected
   `linux/amd64` child digest, config digest, and source type after import.
3. Security approval covers only the AppProjects in `root/bootstrap.yaml`, the
   three namespaced Role/RoleBinding pairs in `security/rbac.yaml`, and the three
   ValidatingAdmissionPolicy/Binding pairs in `security/admission.yaml`.
4. G3-P approves the exact non-secret issuance plan and authority for
   fresh-isolated credentials and a fresh-sandbox CA. G3-R is produced only at
   `credential-bootstrap`: record non-secret issuance receipt hashes,
   metadata/key names and fresh UIDs. Existing Secret or CA
   read/export/transfer is a separate approval and is not part of this plan.
5. Execute the 15-step `bootstrap-dag.yaml`, recording the materialized profile
   digest, live-adapter input-set digest, cluster UID, resource UIDs,
   input-reference digests, status, and
   timestamps after every step.

A separately hash-approved partial prefix may end at `controllers`. It does
not issue credentials or apply the security boundary, RBAC, admission, custom
resources, DB or workloads. Its journal is partial evidence, not full G2 or
PLAT-217 acceptance. Full resume remains blocked unless G3-R is safely
observed; the current adapter does not yet provide that metadata-only
Secret/CA receipt path.

The prefix is an offline contract only until native G2 is satisfied. Its live
gate continues to require applicable raw G2 checks to be `passed`; the runner
does not translate an external exception disposition into a raw pass. Current
raw `failed`/`unrun` inputs therefore reject the prefix before mutation.

### Argo CD cache readiness before any root sync

The in-cluster registration is restricted to `fresh-control`, `fresh-account`
and `fresh-observability`, with `clusterResources=false` and no embedded
credential. `argocd-cm` includes only the fixed desired namespaced GroupKinds.
The three namespace Roles must expose the same `get/list/watch` union for those
GroupKinds while retaining their existing exact-name write rules. Secret is
not in the cache inclusion set and no cluster-wide Secret read is granted.

Before a root operation, prove every included resource is listable in every
configured namespace, cluster cache sync has no forbidden/unauthorized error,
both root Applications compare without `ComparisonError`/`UnknownError`, and
no desired GroupKind was skipped. An application-controller Pod that is merely
Ready does **not** satisfy this gate. `resource.respectRBAC=normal` is not used
to hide a missing required kind. A direct settings/registration change is
expected to invalidate the v3.5.2 cache without a restart; failure to converge
is STOP, and any Pod replacement requires its own approval. A later Helm
upgrade changes the ConfigMap checksum and may replace application-controller,
repo-server and server Pods, so that rollout is not implicit in this gate.

This source tree carries the cache scope together with the separately reviewed
admission fixes (CEL delimiter repair, exact kube-controller-manager service
accounts, and the Istiod public-CA exception) and the reviewed Grafana safe
17-variable, 512 MiB, readiness-only, Recreate desired state. Publication and
live application are still separate decisions: compare the exact integrated
tree with current live bytes, render it, and obtain the required approval
before any sync or Helm upgrade. Never restore the older admission or Grafana
configuration from a partial tree.

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
the runtime-approved sandbox; its disposable empty-data PVC is recovered by
recreating the sandbox from the materialized locks and retained G2 archives,
not by reading the existing environment. Retain redacted journals and hashes
until the independent GPT-6 review disposition, then let the named G2 owner
delete the archives under the approved retention rule.
