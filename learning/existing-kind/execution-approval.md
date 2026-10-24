# Consolidated execution decision — draft, not yet applied

This replaces the first-batch-only approval proposal. One decision should cover
the reviewed execution phase below; do not ask separately for each row. The exact
rendered files, hashes and identity lists accompany this record. No credential or
live authority has been created. Remaining ordinary code work is listed at the end.

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
   the exact 21 existing CRDs and the exact two proposed webhook objects. No
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
ConfigMap plugin informer follows the new watch namespace. Do not add a second
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
  workload-spec write or merge permission. Final adapter/role agreement is a
  remaining code gate below.
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

## Remaining ordinary code gates — no extra permission is requested for these

1. Wire the final root/child project sources to the captured parents without
   duplicate argocd-cm/CRL/controller ownership. CRLs are generated; the composed
   argocd-cm has one owner. Captured parents do not imply live adoption.
2. Remove the deleted-kind/trial-branch pins from the active adapter/receipt
   identity while retaining historical evidence. Decide and implement the child
   Application source ownership together: a root must not reset a release's
   reviewed revision or mode-specific ignore pointers. The minimal intended
   protocol is Git-owned `main` source/definition plus exact merged sync revision;
   any needed definition-Application operation must be included in the final
   named Backstage Role. Do not mask image/strategy drift or retain a competing
   direct spec path to avoid implementing that agreement.
3. Finish scoped checks/independent review at the final proposal SHA, then ask
   for one concrete execution decision. Do not treat this draft as ready for
   application while these gates remain open.

## Existing volume identities (no destructive action selected)

See `inputs/pv-identities.json` and the rendered `existing-pv-decision.json` for
PV UID and claim UID. The human-readable exact four application pairs follow.

| Namespace/PVC | PV | PV UID | Claim UID | Policy |
| --- | --- | --- | --- | --- |
| learning-services-20261018/learning-payment-data | pvc-247463c7-eec2-4942-b50a-25ca810f0387 | b03c8e43-4867-47fb-825e-7f9de15049fa | 247463c7-eec2-4942-b50a-25ca810f0387 | Delete |
| learning-services-20261018/learning-notification-data | pvc-3b432a3c-2f56-427d-bc93-ddabd02d0d73 | a85cf840-ab32-467f-a3e2-fed8e5fc9643 | 3b432a3c-2f56-427d-bc93-ddabd02d0d73 | Delete |
| learning-services-20261018/learning-order-data | pvc-9cc73725-b2a9-4aff-81ee-e48bb75d732c | 9bb7248a-713e-4b37-b300-38d8ae2dcdeb | 9cc73725-b2a9-4aff-81ee-e48bb75d732c | Delete |
| learning-services-20261018/learning-account-data | pvc-9ea2064f-271a-4a97-af7f-110750dc3af6 | 2ecca1c3-5b5a-42ee-838f-c63d7944ffae | 9ea2064f-271a-4a97-af7f-110750dc3af6 | Delete |
