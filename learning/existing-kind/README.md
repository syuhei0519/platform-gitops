# Existing learning kind: migration control record

PLAT-196 / PLAT-193 implementation start, 2026-10-24 UTC.
This directory is a migration proposal and read-only evidence, not an installed
root. Do not apply `bootstrap/root-application.yaml` or the platform-lab root to
this cluster. No workload or access configuration has been changed in this stage.

## Fixed target and source

* Context: `kind-learning-20261018-69c763`; Kubernetes `v1.36.4`.
* Cluster identity (`kube-system` UID): `bf8f9b86-9624-4ee6-a1e4-509aa63ce318`.
* Reuse Argo CD Core `v3.5.2` in `learning-progressive-control`.
* platform-gitops: `908afccb1557aa1e645562bc95f70156ce6387b5`.
* application-manifest: `85b4d70e5ea44ff97176b60a6b1641634b167ad0`.
* backend-app: `584c0fb2a951703ba2f2b24db58e1bc4e741071f`.
* frontend-app: `6f48d1a6d93d02cab6c2c71fb037ea40a0990d14`.

The current Jira bodies and AC were fetched for PLAT-196, 197, 198, 199, 193,
194, 195 and 177. Their registration-time execution pause is superseded by the
user's implementation instruction; new credentials, expanded authority and
security changes still require a concrete separate decision.

## Observed state

The snapshot contains 572 objects across 81 discovered listable API resources,
plus a metadata-only Secret table. Events and Secret bodies are intentionally
excluded. `inventory.json` preserves identity, parent ownership, tracking, image,
storage, namespace and RBAC facts; it is not a desired-state manifest.

* 12 namespaces, 14 Deployments, 7 StatefulSets, 2 DaemonSets, 6 PVC/PV pairs.
* 32 Pods: 28 running Pods and four completed migration Pods. The four service
  APIs, their PostgreSQL instances, observability and Argo are running.
* Only `lab-local-sonarqube` and `lab-local-sonarqube-probe` are Applications;
  both are Synced/Healthy. Existing services have no Argo tracking.
* Istio CRDs and CA/leader remnants exist, but there is no running istiod,
  webhook configuration or Istio routing object. The mesh fixture is plain.
  A CRD or namespace is not evidence of an operational mesh.
* Rollouts `v1.10.1` watches only `learning-services-20261018` with
  `--namespaced --namespace learning-services-20261018`. It cannot reconcile
  a separate Account namespace in its current configuration.
* Frontend and Backstage are not deployed in this cluster. Their repository
  existence is not a live baseline. The Account adapter still pins the deleted
  `kind-plat171-account` and trial Git branch; changing YAML alone is insufficient.
* Four existing application DB PVs have reclaim policy **Delete**; the two Sonar
  PVs have **Retain**. All six PVCs are Bound. Do not delete or recreate them.
* Node reservations: CPU 3100m/20 cores; memory 6498Mi/about 15.5Gi. Limits:
  CPU 11100m, memory 12116Mi (76%). Disk: 932Gi available on the node filesystem.
  The Metrics API is absent; these are reservations and disk readings, not
  measured CPU/memory utilization or a capacity acceptance result.
* Existing UI PID 345454 still listens only at `127.0.0.1:18080`; it is untouched.

## Ownership and migration order

`ownership.json` maps every inventoried object to an intended parent, Git source
or explicit bootstrap/generated/Secret exception. Proposed ownership is not
claimed as live Argo tracking. An unresolved source or immutable-field conflict
stops adoption of that object.

1. PLAT-196: freeze target, baseline, source/field ownership, access proposal,
   immutable checks and recovery boundaries. This task does not deploy.
2. PLAT-193: finish Account budget/isolation using the same inventory. Measure
   current utilization and image availability before accepting the budget.
3. PLAT-197: bootstrap only approved access/registration/root; root and children
   have automated prune off, no cascading deletion finalizers, no Force/Replace.
   Adopt namespace/foundation declarations, existing DB parents and ordinary
   workload parents in small batches. Keep names, selectors, PVC bindings,
   image/config and Secret references unchanged at initial adoption.
4. PLAT-194: integrate the existing release capability into a dedicated Account
   namespace and the same Argo Core; bring up the missing mesh under GitOps after
   security review. Fix server-side adapter targets and evidence identity together.
5. PLAT-195 then PLAT-198: after integrated smoke, retire the dedicated-kind
   entrypoints, then ordinary kubectl/helm deployment routes across repos.
6. PLAT-177: five Account cases and required negative cases on this cluster.
7. PLAT-199: resource coverage, Git update/drift/revert/reconcile and baseline
   acceptance. Do not mark Done before actual evidence exists.

Platform declarations, namespace ownership, Applications/AppProjects and shared
controllers belong to platform-gitops. Application/API/DB workload declarations
belong to application-manifest. Application source and image provenance stay in
backend-app/frontend-app. Existing local images can be adopted without publishing
GHCR packages; image publication is not required by the first migration batch.

The old README's namespace bootstrap-only ownership applies to platform-lab.
For this existing-kind target, namespace declarations should be adopted by the
new protected foundation Application after the explicit access decision.

## Required access/security decisions

See [execution-approval.md](execution-approval.md). Existing permissions cannot perform
the requested migration. No RBAC, registration, credential, webhook or security
setting in this proposal has been applied. Do not reuse old SAST token approval.

## Safety and recovery contract

* Render the exact proposed revision and compare with live before each batch.
  Check Deployment selectors, StatefulSet serviceName/selector/claim templates,
  Service clusterIP/ports and PVC storageClass/accessModes/volumeName/size.
* Stop on pending operations, tracking/Helm owner conflicts, namespace identity
  changes, unexpected Pod replacement, missing Secret keys, data/API regression,
  cache Forbidden/metadata failures or unreviewed image changes.
* Never turn on whole-root pruning, Force or Replace. Protect Namespace, PVC,
  PV and Secret from deletion. Parent workload declarations manage generated
  Pods/ReplicaSets/EndpointSlices; do not freeze these objects in Git.
* Rollouts alone owns dynamic route weights, subset hashes and status. Limit
  ignoreDifferences to those exact resource/JSON pointers and use
  RespectIgnoreDifferences=true. Image, strategy and ordinary config remain
  Git-owned and drift-detectable.
* Existing Secret values stay outside Git and evidence. Bootstrap, initial
  authorization/root insertion, Secret provisioning and individually approved
  break-glass recovery are exceptions, never ordinary workload deployment.
* Initial adoption rollback means suspend the affected sync, inspect the
  operation, revert Git to the known manifest revision and sync without pruning.
  Do not delete an Application with cascading cleanup or run helm uninstall.
  DB schema/data rollback is not guaranteed by a manifest revert.
* Sonar Applications, restricted registration, identity and PVCs remain on their
  existing source/cache boundary. Reuse completed SAST/Sonar evidence.

## Minimum sufficient validation matrix

| Stage / AC | Required evidence | Current result |
| --- | --- | --- |
| 196 target/resource/ownership | context, namespace UID, per-resource inventory, source and exception mapping | inventory captured; ownership proposal pending review |
| 196 safe adoption | exact render/live and immutable/owner/cache review by batch | pending implementation/access decision |
| 193 integration budget | reservations plus actual usage, ports, bounded Account/mesh/DB/load budget | reservations measured; Metrics API absent; actual use pending |
| 197 adoption | tracking, Sync/Health, endpoint/API, DB continuity before/after each changed batch | not run; no live changes |
| 194 integration | Backstage→reviewed Git→Argo→Account smoke and 10/50% traffic during sync | not run |
| 195/198 retire entrances | file-level search, guarded/deprecated historical entrypoints, new smoke | references identified; changes gated on new smoke |
| 177 Account acceptance | manual/auto/abnormal/missing/low-sample/Rolling plus negatives, restart and persistent Git recovery | not run; historical isolated trials are not acceptance |
| 199 whole cluster | fixed revisions, coverage, bounded safe drift, Git revert/reconcile, APIs/data and other apps | not run |
| All changed code | only affected targeted checks, required CI and independent review at final SHA | pending |

No broad suite, CodeQL/Sonar rescan or historical capability rebuild was run.

## Updated data condition and implementation progress

The user clarified that disposable learning data need not survive migration if
recreation is actually proven. We selected in-place adoption because it needs no
old-PVC deletion or Retain mutation. Destructive recreation is an optional future
path requiring exact data/identity and initialization evidence. Credentials, CA
and Sonar remain separate. The offline renderer and captured public parent
declarations prepare the next stage; no reconstruction or live sync has run.
