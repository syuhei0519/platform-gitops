# Consolidated execution decision — scope fixed, not yet applied

This replaces the first-batch-only approval proposal. One decision should cover
the reviewed execution phase below; do not ask separately for each row. The exact
rendered files, hashes and identity lists accompany this record. No credential or
live authority has been created. The source wiring/adapter implementation is complete; required CI and final review are separate execution gates.

## Minimal selected configuration

Reuse this kind, Argo Core, controller ServiceAccounts, CA and local image artifacts.
Keep the four existing service databases and their PVCs in place: this entails
less change than deleting/recreating them and requires **no Retain policy patch**.
Preservation is a chosen minimal-change path, not an absolute acceptance condition.
The user's updated condition permits disposable learning data loss after proven
recreation; it does not classify credentials, CA or Sonar state as disposable.

Create only `learning-account-release` for Account acceptance, with its own DB,
1Gi `standard` PVC, Secret and bounded release workload. Do not recreate the old
dedicated kind or add another Argo/Rollouts controller.

| Data class | Current classification / reconstruction source | Decision |
| --- | --- | --- |
| Existing four service DB volumes | learning application schema/data; init ConfigMaps, source-controlled migration code and parent declarations are available; actual row importance and fresh reconstruction have not been proven | retain in place for adoption; no destructive operation requested |
| New dedicated Account DB | isolated acceptance fixtures; init declaration and reviewed image migration establish schema; new PVC supports restart acceptance | create empty DB/PVC; prove initialization and bounded restart; deletion is not part of this approval draft |
| Existing Secret values, Istio CA and connection metadata | external credentials/trust/recovery material, not application test rows | preserve; never export private values to Git/evidence |
| Sonar DB/PVC/configuration | completed SAST/Sonar service and configuration | preserve existing Application/cache/data and reuse evidence |
| Prometheus/Grafana emptyDir | disposable metrics/cache/session state, ConfigMaps are reconstruction source | do not restart merely to adopt; record actual restart limitations |

Recreation alternative: after a successful fresh Account DB initialization and API
smoke, a future reviewed Git change could recreate a selected old learning DB/PVC
from the same init/migration declarations. That would erase its old rows and is
not needed to complete this adoption. No deletion permission is requested on the
basis of the user's general data-loss tolerance. An irreversible deletion still
requires exact identities, lost data class and actual initialization evidence.

## One execution-phase authority and security scope

1. **Argo reconciliation:** six namespaces (`learning-services-20261018`,
   `learning-progressive-control`, `observability`, `learning-istio-demo`,
   `istio-learning-system`, `learning-account-release`) get identical cache read
   coverage for the included non-Secret kinds. Writes are grouped per namespace
   in `argocd-access.yaml`: ordinary parents in existing namespaces, control
   Application/AppProject/configuration, Istio parents, and Account Rollout,
   AnalysisTemplate/Job/DB parents. No new direct Secret API access, delete,
   bind, escalate or wildcard resource permission is granted to Argo.
2. **Cluster foundation:** cluster list/watch reads cannot be restricted to an
   explicit name set while supporting the Engine cache. The proposal discloses
   this cluster metadata read authority. Updates/patches are restricted by
   resourceNames to the existing 12 namespaces plus the new Account namespace,
   the exact 21 existing CRDs and the exact two proposed Istio webhook objects plus ValidatingAdmissionPolicy/Binding `backstage-account-operation-only`. No
   Namespace/CRD deletion or namespace finalization is granted. CRD schema change
   is not intended: initial declarations retain the captured live spec and the
   installed versions. Name sets are in the rendered grant and inputs JSON files.
3. **Bootstrap trust boundary:** initial creation of the exact named namespace,
   Roles/Bindings, ServiceAccounts, two webhooks, registration and protected root
   is an operator bootstrap/security action. Argo does not receive unrestricted
   RBAC administration to self-authorize. Security grants remain reviewed Git
   declarations with explicit bootstrap ownership; ordinary workload updates
   must use Git/PR/Argo. The declared bootstrap exception must be reviewed against
   PLAT-196/199 rather than silently described as complete Argo tracking.
4. **Registration/cache:** one new registration `learning-gitops-in-cluster`,
   server `https://kubernetes.default.svc.cluster.local:443`, six namespaces above,
   clusterResources=true. No project binding on the registration itself because
   both foundation and Account projects use it; their explicit destination/source
   allowlists enforce project access. Preserve default Project's empty destinations.
   Use the verified existing projected-SA exec provider, public CA and TLS
   serverName `kubernetes.default.svc`; issue no fixed bearer token. Preserve both
   old registrations. Append only URL-scoped non-Secret inclusions; do not change
   the old/Sonar cache boundaries or repeat the known cache regression.
5. **Effective credential authority:** Deployment/StatefulSet writes permit
   mounting existing namespace Secrets and selecting existing ServiceAccounts.
   This indirect authority is part of the decision even though Argo receives no
   new direct old-DB Secret API read. Argo can also change its own configuration
   within the reviewed namespace. Initial adoption preserves commands, images,
   selectors, Secret refs and SA names except the explicit controller changes.

## Exact Istio admission, certificates and controller change

Render the existing vendored Istio 1.31.2 chart; pilot/proxy images remain pinned
to the existing versions.lock.json digests. Discovery selects only the new
Account namespace and `istio-learning-system`.

* New MWC: `istio-sidecar-injector-learning-1-31-2-istio-learning-system`.
  Both generated webhook segments gain an explicit Account namespace-name
  constraint, retain opt-in revision/object conditions, and use failurePolicy=Fail,
  timeout=10s. Existing mesh-demo/service Pods cannot become injected by restarting.
* New VWC: `istio-validator-learning-1-31-2-istio-learning-system`, scoped to
  Account and Istio control namespaces, failurePolicy=Fail, timeout=10s. No extra
  default cluster-wide validator is installed. Bring up the endpoint and CA bundle
  before creating matching Account configuration. A readiness failure stops there.
* The namespace becomes opt-in for `learning-1-31-2`. Injection uses the existing
  no-CNI init redirect pattern: an init container with root/NET_ADMIN/NET_RAW is a
  real security change, confined to these new Account/client Pods.
* Existing `istio-ca-secret` is reused. Public certificate fingerprint is
  `5B:67:E6:94:C8:42:59:EB:82:FC:6F:42:73:91:0A:FD:18:13:5D:BE:48:78:F6:6F:6F:C6:E0:BA:3F:68:30:E2`,
  valid until 2036-10-15. No CA private value was selected/exported. Permit normal
  serving/workload leaf issuance from that CA; workload leaf TTL 1h/max24h. Do not
  rotate/replace the CA root. Compare root fingerprint/Secret identity after startup.
  Serving certs/caBundle/root-cert ConfigMaps are controller-owned, not Git values.
* Pilot gets the rendered non-Secret discovery permissions and TokenReview /
  SubjectAccessReview create. Cluster reads include public ConfigMaps, Pods,
  Services, endpoints, namespace/node/CRD and Istio configuration; they are broader
  than discovery selectors. No cluster Secret grant or unrelated ingress/status/
  Gateway API deployment administration is copied from the default chart. CA Secret
  read/create/update stays only in the Istio control namespace; delete is removed.
  Public root-CA ConfigMap writes stay in Account/control namespaces. Any missing
  authority/Forbidden result stops startup; do not silently widen access.
  ConfigMap create cannot be name-restricted by Kubernetes RBAC; update/patch/delete
  is limited to `istio-ca-root-cert` and `istio-ca-crl`, and the unused broad report
  ConfigMap delete grant is removed. The chart's control-namespace `gateways create`
  is intentionally retained for the validator controller's server-dry-run invalid
  Gateway readiness probe, not an ingress Gateway deployment. A compromised pilot
  could persist a Gateway in that control namespace; this effective authority is
  included in the same decision. No such Gateway is declared or created here.

## Reuse the existing Rollouts controller

There are no active Rollout objects in the captured cluster. Retain Deployment
`learning-services-20261018/learning-rollouts`, its selector, SA, resource/security
settings and UID. Change only its watch namespace to `learning-account-release`,
add `--istio-api-version=v1`, and select the already built controller+metric-plugin
image `learning.local/account-rollouts@sha256:966adba896524519bd1653b31ade97b624dd5cbf83623f7cdeb5c5708aaa69e0`.
The plugin source tree is byte-identical between source66a1d4e and current main;
no new implementation/build is needed. Verify existing archive/image identity
before importing its cache into this kind; no package publication is required.

The new Account Role/Binding targets the existing SA from the old namespace. It
allows only the one named Rollout status, Account-namespace generated RS/AnalysisRuns,
lease/events, and the one named Istio route/subsets. No Secret rule is added. The
plugin ConfigMap, notification filters and leader lease remain in the existing
controller Pod/SA namespace learning-services-20261018: v1.10.1 uses
defaults.Namespace(), independently of --namespace. The old filtered Role is
reused; new Account ConfigMap/lease permissions were removed. Do not add a second
controller or grant cluster-wide workload writes. Prove this scope change and
plugin readiness before Account acceptance; rendering is not a runtime pass.

## Credentials, storage and Backstage session

* New DB: `learning-account-release/learning-account-db`, keys `admin-password`
  and `app-password`, independent random values, used by postgres and the limited
  `account_app` role respectively. Store only in that namespace Secret; use private
  stdin provisioning, no CLI literal/log/Git value and no old-DB credential copy.
  Do not add Windows Credential Manager entries just for this DB. No local backup
  is required for disposable fixtures. Retain the Secret during operation; specify
  manual rotation no later than seven days after creation and record the deadline.
  New DB initialization and API/schema smoke remain unexecuted. No claim of
  automatic password expiry or DB rollback guarantee is made.
* New PVC: `learning-account-release/learning-account-data`, standard/RWO/1Gi.
  The parent StatefulSet mounts that PVC, replacing the old dedicated-trial
  emptyDir design. PVC/Namespace pruning and deletion are disabled. No old PVC is
  moved or renamed. Exact new PV identity will be known only after binding.
* Backstage SA `learning-account-release/backstage-account`: named Rollout get /
  status get+patch, Account Pod/RS/AnalysisRun reads, named route reads; control
  Application get+patch only for Account release/definition Applications needed
  by the selected Git-source ownership protocol. No Secret, exec, Pod deletion,
  workload-spec write or merge permission. The adapter and named Role now agree.
* API token: TokenRequest with default Kubernetes API audience, requested 3600s;
  use returned expirationTimestamp as authority. Store only a dedicated kubeconfig
  under user+SYSTEM ACL Temp, never standard kubeconfig. A single approval covers
  bounded renewal of this same SA/scope during at most 24h of acceptance, with an
  absolute session cutoff checked by the adapter; no unattended renewal. Stop UI
  write mode and remove the task credential file at session end. Already-issued
  JWTs expire naturally; file deletion is not claimed as JWT revocation.
* Reuse existing GitHub wrapper authentication only for application-manifest
  profile read/proposal refs/Draft PR creation; existing coordinator rejects other
  repositories, arbitrary paths and force/main update/merge. No new GH token or
  SAST token reuse. Release merges still pass review/required CI.
* Backstage official development guest identity is enabled only in private config
  for this loopback session, with one explicit allowed actor. Other local processes
  able to connect can act as that guest; this security limitation is part of the
  decision. Bind UI/backend to 127.0.0.1:3000/7007, cap session to24h, persist only
  protected audit/coordinator state and do not expose outside the host.

## Gate and recovery

Initial root/children have auto-prune off, no cascading finalizers, Force or
Replace. Adopt rendered/live-identical ordinary parents in small groups, then
execute the reviewed controller/Account changes. Before each batch, verify fixed
cluster/namespace identities, exact source SHA, immutable fields, no pending
operation and no tracking conflict. Stop on unexpected Pod replacement or API,
cache, data/schema, cert or other-app regression. Revert Git/sync the affected
batch without pruning. Do not delete the kind, CA, Secret or PVC as rollback.

Only affected static/render checks, required CI, independent review, integration
smoke and the AC-required five cases/negative/drift/revert/restart tests are needed.
The completed capability tests and SAST/Sonar results are reused at unchanged source.

## Final source/operation agreement

The root creates seven manual children: foundation, existing services, shared
observability, plain mesh fixture, reused Rollouts, Account DB/client foundation,
and `account-release-definition`. It does not declare a competing direct Account
workload Application. Every root/child has pruning off and no cascading finalizer.

The definition chart consumes the Git profile and Argo's resolved Git revision via
Helm parameter `manifestRevision=$ARGOCD_APP_REVISION`. It declares the Account
Application's exact commit and Canary-only ignore pointers together. Re-syncing
the root/definition therefore reproduces Git state instead of resetting a portal
patch. Argo documents the resolved-revision build variable and Helm substitution:
[build environment](https://argo-cd.readthedocs.io/en/latest/user-guide/build-environment/),
[Helm parameters](https://argo-cd.readthedocs.io/en/latest/user-guide/helm/#build-environment).

The adapter reads observed `status.sync.revision`, validates the merged main commit
and unchanged tree, CAS-syncs the definition from protected main, waits for that
same resolved SHA and child spec, then CAS-syncs the workload at that exact SHA.
It never patches Application spec/source/ignore fields. A moved main, replaced UID,
foreign operation or generation change is fail-closed. Receipt checks bind the
actual Application UID/observed SHA and the reused controllers' real namespaces.
The fixed existing-kind context is mandatory; deleted-kind/trial branch inputs
are rejected. Token expiry and an absolute maximum24h session window are enforced.

### Enforced Backstage credential boundary

Kubernetes Application patch RBAC alone includes spec modification. To avoid
implicitly giving this new credential access to the other projects in the reused
Core, one native policy and binding named `backstage-account-operation-only` are
added. They match only two named Applications in learning-progressive-control.
For the new Backstage SA they enforce unchanged spec/ownership metadata, no active
operation overwrite, no retry/source/resource/manifest/strategy override, no prune,
and only the explicit RespectIgnoreDifferences option. Definition sync may use
main; workload sync must use its Git-owned exact source SHA. During any actor's
active workload sync, spec changes are rejected until the operation clears.
Other actor updates are otherwise unaffected. No external webhook service or new
certificate is introduced. Argo may reconcile these two native policy objects by
name; initial operator bootstrap creates them before granting/issuing portal access.

Server dry-run accepted the policy/binding without storing them. This does not
prove enforcement. After approval and installation, inspect type-check warnings
and use operator impersonation/dry-run requests to confirm allowed operations and
denial of spec/source/project/destination/revision/prune/Force overrides before
issuing the portal token. Any failure stops activation without widening authority.

### Observability/data footprint

The existing receiver is namespace-specific. Keep it and its old metric labels
unchanged. Add just `observability/account-release-otel-collector` with the same
pinned image (50m/64Mi request,500m/256Mi limit) and one new-Account Pod-read
Role/Binding. Its projected Pod identity is runtime-managed, not a fixed Secret.
Append its target to the existing otel-application scrape job with the new namespace
label and append the existing scoped Envoy job. Prometheus alone receives a checksum
restart to load this configuration; its emptyDir metric history may reset. This is
a planned disposable observability effect, not a DB/CA/Sonar reset. Preserve all
old scrape/alert entries and prove old targets after warmup.

The new DB init/migration Job and bounded traffic client are Git declarations.
They have not run. Reuse reviewed fixed API/client/controller images; verify cache
artifact hashes rather than rebuilding unchanged code or publishing packages.
Peak new Account+mesh/client/collector/istiod requests are approximately1160m CPU
and1440Mi memory; combined limits add about5700m/3456Mi. Existing reservations were
3100m/6498Mi. Limits are ceilings, not usage guarantees; measure actual free memory
before session start and stop if node/host pressure or less than2Gi available
memory threatens baseline services. The local portal is additionally limited to
a1Gi Node heap. Do not claim capacity or functional acceptance from this estimate.

## Execution gates

Scope and intended authorities are fixed. One human execution decision may now
cover these specific changes and credential lifetimes. Merge/apply remain gated
on required CI and final targeted review; actual alias/cache, native admission,
leaf issuance, new DB initialization/API and non-regression are checked after the
approved small batches. These runtime checks are not completed or substituted by
21 passing adapter/receipt tests. No live grants, credentials, admission activation,
workload sync, deletion or data recreation has occurred.

## Existing volume identities (no destructive action selected)

See `inputs/pv-identities.json` and the rendered `existing-pv-decision.json` for
PV UID and claim UID. The human-readable exact four application pairs follow.

| Namespace/PVC | PV | PV UID | Claim UID | Policy |
| --- | --- | --- | --- | --- |
| learning-services-20261018/learning-payment-data | pvc-247463c7-eec2-4942-b50a-25ca810f0387 | b03c8e43-4867-47fb-825e-7f9de15049fa | 247463c7-eec2-4942-b50a-25ca810f0387 | Delete |
| learning-services-20261018/learning-notification-data | pvc-3b432a3c-2f56-427d-bc93-ddabd02d0d73 | a85cf840-ab32-467f-a3e2-fed8e5fc9643 | 3b432a3c-2f56-427d-bc93-ddabd02d0d73 | Delete |
| learning-services-20261018/learning-order-data | pvc-9cc73725-b2a9-4aff-81ee-e48bb75d732c | 9bb7248a-713e-4b37-b300-38d8ae2dcdeb | 9cc73725-b2a9-4aff-81ee-e48bb75d732c | Delete |
| learning-services-20261018/learning-account-data | pvc-9ea2064f-271a-4a97-af7f-110750dc3af6 | 2ecca1c3-5b5a-42ee-838f-c63d7944ffae | 9ea2064f-271a-4a97-af7f-110750dc3af6 | Delete |

## Large CRD field ownership decision

Existing CRD bodies exceed client-side last-applied annotation capacity. Only
these21 captured same-spec CRDs use ServerSideApply=true with
ClientSideApplyMigration=false (the exact v3.5.2 constant). No Force=true or
Replace=true resource recreation is configured. Argo v3.5.2's SSA backend itself
sets ForceConflicts=true; this is distinct from delete/recreate but is real field
ownership authority and is included in this execution decision. All21 passed a
non-persistent server-side dry-run with force-conflicts=false and the same
argocd-controller field manager. Any changed live spec or ownership conflict at
execution preflight stops this batch; do not retry a conflict forcibly. This is
not a claim that the backend conflict flag is disabled. The pinned source is
[resource operations](https://github.com/argoproj/argo-cd/blob/e258ee23c3e52266d407572f4bcdfe7d9ed36cb5/gitops-engine/pkg/utils/kube/resource_ops.go).

## Credential cleanup included in this one decision

At the portal session cutoff disable its writes, stop only the new portal-owned
processes and remove its protected task kubeconfig. Remove only the two newly
created RoleBindings `learning-account-release/backstage-account` and
`learning-progressive-control/backstage-account-application`, after UID verification,
to revoke their effective permissions while any short token naturally expires.
Keep the SA/Role declarations, native policy and DB/CA/Sonar Secrets. This is a
reversible authorization cleanup, not data deletion. Never remove the existing
Argo, Rollouts, Sonar or unrelated bindings. Clip any requested renewal to the
remaining approved window and reject a returned expiry beyond that window.

The resolved-revision template and one native policy/binding are the entire new
operation boundary; no general evidence service or external admission server is
introduced. Live positive/negative authorization checks occur before token use.
