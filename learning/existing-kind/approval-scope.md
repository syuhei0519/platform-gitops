# Decisions required before live migration

Status: proposed, **not authorized by this document and not applied**.
The user's implementation authorization excludes new credentials, expanded
permissions and security changes without a concrete separate decision.

## First adoption batch: concrete additional Argo authority

Subject remains
`system:serviceaccount:learning-progressive-control:argocd-application-controller`.
Do not introduce cluster-admin, a new ServiceAccount or a fixed bearer token.
`access-proposal.yaml` is the exact additive Role/Binding proposal for the first
ordinary workload and observability adoption batch. It intentionally grants no
direct Secret API permission and no delete verb. The old roles are not replaced.

| Namespace | New authority | Why / risk |
| --- | --- | --- |
| learning-services-20261018 | ConfigMap, PVC, StatefulSet, Deployment get/list/watch/create/update/patch | adopt four API/DB parents and init configuration; can change API code/config, restart workloads and alter claim requests; adds no PVC delete or direct Secret API permission, but workload mutation enables indirect Secret use/access |
| observability | ConfigMap, Service, ServiceAccount, Deployment get/list/watch/create/update/patch; Pod/ReplicaSet read | adopt existing collector/Prometheus/Grafana; configuration could affect collection or expose data, so initial manifests must be identical |
| learning-istio-demo | same ordinary namespace kinds as observability | adopt plain existing mesh fixtures; does not install mesh or change injection |
| learning-progressive-control | Application/AppProject create | root must create child Applications/projects; existing update/patch authority is retained; a malicious allowed Git source could change child targets, so projects/repositories/destinations must remain explicit |

All four cache namespaces additionally receive get/list/watch on ConfigMap, PVC,
Service, ServiceAccount, Pod, Deployment, StatefulSet, ReplicaSet, Application,
AppProject, Rollout, AnalysisTemplate and AnalysisRun. The Engine caches included
kinds across every namespace in that registration, including namespaces with no
objects of a kind. This consistent non-secret read coverage is necessary to avoid
the already diagnosed Forbidden cache failure. The exact eight YAML documents
(four Roles and four Bindings) are authoritative for this proposal.

Namespace-wide create/update/patch cannot safely be described as access to only
the currently listed names. Initial AppProject kinds, repo URLs and destinations
must bound reconciliation. Namespace Role permissions remain an independent
trust boundary. Existing `argocd-learning` already grants some deletions and
control-namespace Secret reads; this additive proposal neither broadens nor
claims to remove those old permissions.

**Indirect credential risk:** Deployment/StatefulSet creation or PodTemplate
changes can mount existing namespace Secrets into a Pod or select an existing
ServiceAccount. A compromised allowed Git source could therefore obtain Secret
values through workload execution or use that ServiceAccount's permissions even
without Secret API get/list/watch. The explicit decision must include this
effective authority. Initial adoption must preserve existing images, commands,
Secret references and ServiceAccount names; review later changes to those fields.
An AppProject kind allowlist alone does not constrain PodTemplate fields.

### Cache/registration change required with that batch

The first batch needs ConfigMap/PVC/StatefulSet in the old application namespace
without old DB Secret access. Do **not** extend the Sonar `:443` registration to
that namespace: its Secret inclusion would require forbidden old Secret lists.
Do not repeat the already demonstrated cache regression or enable respectRBAC
to conceal metadata loss.

Proposed isolated cache URL: `https://kubernetes.default.svc.cluster.local:443`,
name `learning-gitops-in-cluster`, project `learning-existing`, namespace list
`learning-services-20261018,observability,learning-istio-demo,learning-progressive-control` and
`clusterResources=false`. Verify this alias resolves to the same API/CA and
verify TLS using `serverName=kubernetes.default.svc` before registration.
Append URL-scoped inclusions for ConfigMap/PVC/ServiceAccount/StatefulSet and
Application/AppProject (needed by the protected root);
retain existing wildcard Pod/Service/Deployment/ReplicaSet/Rollouts inclusions.
Never include Secret on this cache URL.

Registration content contains public CA and the already verified existing-SA
exec provider from `learning/sonarqube/existing-sa-credential.sh`. The controller
reads its own projected token at runtime. No issued token value is stored in
Git, the registration or evidence. This still creates one Argo registration
Secret and changes cache configuration; request explicit agreement to that
specific operation. Leave canonical and Sonar registrations unchanged.

Recovery: suspend the new Applications, check pending operations, remove only
the new registration/append-only inclusions/additive RoleBindings by verified
identity, and retain all adopted workloads/PVC/data. Removing an additive role
is not a workload rollback. Verify both old Applications remain healthy.

## Foundation and Account: further concrete boundaries to review

These requirements are identified but are **not included** in the first-batch
Role manifest. Render their exact security diff and update this decision record
before executing them; this text is not open-ended approval.

1. Namespace/CRD/foundation adoption needs cluster-scoped metadata reads and
   name-restricted writes for the existing 12 namespaces and 21 CRDs, plus
   StorageClass/local-path declarations if classified outside kind bootstrap.
   Argo currently has no such cluster permission. Avoid deletion, CRD replacement,
   Namespace recreation or blanket RBAC administration. Keep bootstrap authority
   itself an explicit reviewed exception rather than granting unrestricted
   `escalate`/`bind` to make self-management appear complete.
2. Istio is absent as a running control plane. Account Canary needs the pinned
   istiod Deployment/Service/ServiceAccount, namespace RBAC, injection/validation
   webhook and CA/serving-certificate lifecycle for the existing revision. This
   changes admission/security behavior. Scope injection to the dedicated Account
   namespace; do not relabel the existing service namespace or mesh fixtures.
   Preserve the existing CA Secret; no CA value has been read or copied.
3. Existing Rollouts is namespaced. Before selecting a controller strategy,
   compare (a) moving its one reconciliation scope to the new Account namespace
   after proving no active old Rollouts, versus (b) one controller with reviewed
   multi-namespace authority. Do not silently add another controller or grant
   cluster-wide writes. Istio VirtualService/DestinationRule dynamic field writes
   also require a narrowly scoped controller grant.
4. Dedicated Account namespace `learning-account-release` needs isolated DB
   credentials (`learning-account-db`: `admin-password`, `app-password`). These
   are new credentials for a new test DB, not copies of existing service DB
   credentials. Define lifetime, protected local storage if needed, cleanup and
   rejection checks before issuance. The old `database.yaml` uses `emptyDir`,
   so it cannot meet durable data/restart acceptance unchanged; use a protected
   PVC and explicitly accept the storage/lifecycle design before applying.
5. Backstage's old runtime adapter expects the deleted cluster/trial branch and
   uses a time-limited credential. Replace fixed server-side target identities
   and reuse only explicitly allowed existing authentication. Any new service
   credential or token for Backstage/controller operations needs its own exact
   scope and lifetime; old SAST/project-token approval does not cover it.
6. The four existing DB PVs have Delete reclaim policy. Proposed protection is
   name-restricted patch of just those four PVs to Retain, without changing
   claimRef/storageClass/volumeName or deleting any claim. This is a storage
   lifecycle/security-relevant change; present the exact four identities from
   inventory before execution. Retain is not a backup or DB rollback guarantee.

No GHCR publication, external exposure, password login enabling, TLS bypass,
UI shutdown, whole-cluster rebuild or irreversible deletion is proposed.
