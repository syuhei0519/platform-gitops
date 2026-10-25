# PLAT-3 / PLAT-5: shared SonarQube with the existing Argo CD Core

This directory contains the pinned shared server chart, manual bootstrap/access
definitions and offline boundary checks. Tests never connect to Kubernetes or
read real credentials. The application SAST changes were merged separately in
backend-app#59 and frontend-app#14. Live validation was performed only after
explicit approval; ticket completion also requires the staged scanner gates.

The inspected cluster is `learning-20261018-69c763`, not a running `platform-lab`.
Argo CD Core v3.5.2 runs in `learning-progressive-control`, using the
`argocd-application-controller` ServiceAccount. Its in-cluster registration currently
contains only `learning-services-20261018`, with `clusterResources=false`.
There were zero Application and AppProject objects at the read-only preflight.

## Reproduced restriction

`argocd-cm` includes Rollout, AnalysisTemplate, AnalysisRun, Service, Pod,
ReplicaSet and Deployment. SonarQube also needs StatefulSet, PVC, ConfigMap,
ServiceAccount, Secret and NetworkPolicy. The old destination Role does not allow
listing these additional kinds. In particular, it cannot read the application DB
Secrets. That restriction must remain intact.

The installed Engine caches a kind across all registered namespaces. Adding
`sonarqube` to the registration while keeping different per-namespace permissions
is therefore not just a new Application declaration. Run the actual fixed-source
fake-client regression as follows:

```bash
python3 learning/sonarqube/check_cache.py --argocd .build/argocd-istio
```

The same upstream checkout is already fixed to
`e258ee23c3e52266d407572f4bcdfe7d9ed36cb5` in CI. The test uses the actual Engine,
fake list/watch clients and a local fake SelfSubjectAccessReview API. No kubeconfig,
token, real Secret or live cluster endpoint is used. The relevant old Role is
projected onto the tested kinds: Service/Pod can be listed; the new kinds cannot.
All proposed kinds can be listed in the new Sonar namespace only.

| Configuration | Actual result |
| --- | --- |
| Both destinations, current default mode | Cache synchronization fails on forbidden list |
| Both destinations, `respectRBAC=normal` | Synchronization returns success, but Secret kind metadata is lost |
| Both destinations, `respectRBAC=strict` | Synchronization still fails when another namespace allows the kind |
| Sonar destination only, default mode | Kind metadata and live object lookup succeed; control case only |

Passing this regression confirms the limitation. It does **not** mean that the
two-destination rollout is safe or that scanner/server acceptance passed. In
particular, `normal` must not be used to hide the metadata failure.

## Live alias integration

On 2026-10-24, the explicitly approved alias was installed in the existing Core
and validated against the real API. `lab-local-sonarqube-probe` used the exact
source commit `34cabb9372b3b345801ca60265a882bac4abac90` to create only the
non-secret `sonarqube-cache-probe` ConfigMap. The operation reached `Succeeded`
and the Application reached `Synced`. This exercises real registration selection,
exec-provider authentication, API discovery, URL-scoped inclusions and cache lookup.
The canonical registration, old namespace Role and controller Pod specification
were checked unchanged. No existing DB Secret permission was added.

The first attempt failed before Application creation because WSL could not reopen
the forwarded stdin as `/dev/stdin` for a public ConfigMap patch. Its added Role,
Binding, registration and AppProject were removed by UID, and the old inclusions
were verified unchanged. The dedicated namespace was retained. After a confirmed
rollback, public metadata patches were passed directly and the integration passed.
Rollback behavior was independently checked with six offline cases, including an
in-flight sync, lost restore response, pending journal, Secret metadata-only reads
and uncertain create outcomes. This is not yet a Sonar analysis acceptance result.

## Live deployment and analysis evidence

The existing Core is reused with a different-URL registration and the existing
projected identity, preserving both destinations. The Sonar-only control case was
not deployed. `lab-local-sonarqube` reached `Synced / Healthy / Succeeded` using the
fixed chart commit `8eadbdb2673592a316d519e1a6df2fb0741c465b`. Sonar was 2/2 Ready,
PostgreSQL 1/1 Ready, and both new PVs were Bound and Retain. Existing 26 Pods stayed
Ready with unchanged UID/image; old Argo registration, Role and Pod spec stayed intact.

Do not add old-namespace DB Secret read access, grant cluster-admin, deploy a second
Core, change the controller image, replace kind, or change the registration scope
as a workaround. Those are not authorized by the diagnostic test or this PR.
The live NetworkPolicy test passed baseline connectivity, default
deny, selective ingress and selective egress. All seven temporary resources were
deleted by verified UID. Production default-deny and DB/DNS allow policies were
then installed before any credential was configured. `vm.max_map_count` was changed
temporarily from 262144 to 524288 without reboot or persistence. Initial admin
password replacement was verified with new-value acceptance and old-value rejection.
TLS checks used the explicit public leaf, hostname, validity and fingerprint, on
the WSL localhost path and Docker `host.docker.internal` path; no validation was disabled.

Sonar Community 26.9.0.129388 analyzed only these main revisions:

| Project | Main revision | Baseline results |
| --- | --- | --- |
| platform-backend-app | c0c2878db3f61be1f8c9c77d2813e12281356a58 | 38 code smells; no vulnerabilities/hotspots |
| platform-frontend-app | 9e49e72df157bc6bf550a24adbd4f267a93efaca | 2 code smells + 1 bug; no vulnerabilities/hotspots |

Go analyzer was 1.43.0.7704, JavaScript/TypeScript 13.8 build 44569 and text analyzer
2.49.0.12346. Both first analyses completed successfully; first-analysis QG had no
evaluated New Code condition. After a second unchanged main analysis, both CE tasks
again succeeded and Sonar way reported `OK` with `new_violations=0`, threshold `>0`.
This is a passing unchanged baseline, not proof of no existing quality findings.
The other three Sonar way conditions (new coverage 80%, duplication 3%, hotspots
reviewed 100%) were not applicable to zero new lines/hotspots. They were not removed.
Both seven-day project tokens were revoked after validation and their old credentials
were confirmed rejected. Admin remains in Windows Credential Manager only.

Semgrep remains in initial observation mode and CodeQL has no new merge ruleset.
The eleven backend executable audit findings were independently confirmed as test
harness false positives; the existing CodeQL High remains an analyzer-model candidate,
not a dismissed result. Policy selection, authorized CodeQL merge protection and its
blocked/corrected PR validation remain separate incomplete acceptance stages.

## Pinned deployment chart

`chart/` embeds the unmodified official SonarSource chart `2026.5.1003`, package
SHA-256 `c85b05ffd646deb9fea7c28463622e42d2a93bbe988625530eda7d6cf6ee96fc`.
Community Build `26.9.0.129388`, PostgreSQL `18.6-bookworm` and the unprivileged
nginx `1.29.5-alpine3.23` TLS sidecar all use fixed image digests in values/templates.
The wrapper adds only the dedicated PostgreSQL, TLS configuration/private service,
PVC and NetworkPolicies. No workload ServiceAccount or privileged init is created.
Sonar and PostgreSQL reserve 5Gi + 10Gi using the existing `standard` StorageClass.
Only these new PVs may be patched to `Retain` after binding; existing PVs are untouched.
PVC pruning is disabled. These volumes are not a backup or a disk quota.

Render with the CI-pinned Helm and validate using `validate.py <rendered.yaml>`.
The render contains 16 resources, including the official chart's empty proxy Secret;
it contains no real credential. Initial sync is manual and pinned to a reviewed
commit. The lab accepts Kubernetes 1.36.4 outside the checked Sonar support matrix
and the chart's stated Argo support limitations; it must be measured, not assumed.

Secrets are operator-created through stdin only after NetworkPolicy proof.
PostgreSQL admin/JDBC/monitoring/TLS key are stored only in this namespace and
Windows Credential Manager; Sonar admin and future project analysis tokens are
Credential Manager only. The public leaf certificate may be saved for explicit
hostname/expiry/fingerprint verification. No private key, password or token is in
Git, a rendered artifact, CLI arguments or chat. Token validity is seven days and
tokens must be revoked after validation. Password rotation deadlines are operational
deadlines, not automatic PostgreSQL or Sonar account expiry.

`access.yaml` records only the dedicated namespace, namespace Role/Binding and
restricted AppProject. It is operator bootstrap, not part of the unrelated root
chart or a new kind environment. `additional-inclusions.yaml` is an **append-only**
fragment for `argocd-cm`, never a replacement for the three existing inclusion entries.
`registration.py <preverified-public-ca-file>` renders the alias registration without
reading any token. Only client-go invokes the separately tested provider inside the
existing Pod. Check the exact kind identity, CA/expiry/hostname, resource capacity and
rollback journal before applying access configuration. Live bootstrap was done
manually with explicit approval; GitOps application contents are pinned, while both
implementation PRs are still Draft/unmerged pending separate workflow approval.

## Alternatives and limits of this test

- Two registration Secrets with the **same server URL** do not create independent
  caches. The controller indexes them by `cluster.Server`; the URL informer returns
  the first indexed registration. Changing the display name, project or namespaces
  does not change that cache identity. Do not create ambiguous duplicate entries.
- In this version, inclusion/exclusion fields are only API group, kind and cluster
  URL. They cannot exclude old-namespace Secrets while including Sonar Secrets on
  the same URL. Excluding the kind globally would also remove Sonar management.
- A **distinct URL spelling** for the same API, such as
  `https://kubernetes.default.svc:443`, has a different cache key. Extra kind
  inclusions could be restricted to that exact URL while retaining the old
  registration, old inclusions and old Role. The additional `EndpointSplitCache`
  test confirms both Engine caches retain metadata/live lookup with those two
  namespace-specific discovery lists and no extra old-namespace read permission.
  It explicitly simulates filtered discovery: the upstream mock does not apply the
  actual resource filter. This is a component test, not an end-to-end proof.

The existing controller's binary reports v3.5.2, commit
`e258ee23c3e52266d407572f4bcdfe7d9ed36cb5`, matching this test source. A read-only
probe from that controller to the explicit-port URL verified the existing CA,
hostname and TLS 1.3, and received `/version` HTTP 200 for Kubernetes v1.36.4.
No credential was needed for that public endpoint. It proves transport only.

The alias does **not** automatically inherit in-cluster credentials: the installed
`Cluster.RawRestConfig` selects `rest.InClusterConfig` only for the canonical
`https://kubernetes.default.svc` value. A separately reviewed authentication route
would be necessary. Reuse of the controller's existing projected ServiceAccount
through an exec credential provider was subsequently approved and validated by
the live probe above. Only client-go consumes the provider output inside the Pod;
no token value is copied into the registration, a log, Git or a local file.
Do not introduce a new static Argo token or disable TLS verification.

### Approved authentication change

`existing-sa-credential.sh` is the exec-provider command embedded in the approved
registration for that distinct URL. `credential_test.py` runs it in the exact
controller image, network disabled, with only a fake token mounted at the standard
projected-token path. It checks valid protocol/one-minute cache expiry and rejects
empty, invalid or missing input without credential output. Do not run it manually
against an existing Pod: its stdout is a credential protocol intended for client-go.

The approved persistent change is a new `sonarqube-in-cluster` registration Secret
in `learning-progressive-control`, containing the alias server URL, only the
`sonarqube` namespace, `clusterResources=false`, public CA and exec-provider command.
The command would run inside the existing controller, use its existing projected
ServiceAccount identity, and send authentication only to the same TLS-verified
Kubernetes API. No new ServiceAccount, static token, token Secret or Windows
credential entry is proposed for Argo. The existing registration/authentication and
old namespace Role would remain unchanged. Only the already proposed Sonar namespace
Role would add workload management for this existing identity.

This differs from canonical in-cluster authentication by reading the same projected
token through the exec protocol, cached for at most one minute, rather than the
automatic `rest.InClusterConfig` path. The registration is persistent access
configuration even though it contains no token value. It was applied only after
explicit approval. No mounted
script, controller Pod change/restart, extra Core or Redis credential is proposed:
the reviewed script would be embedded as the command argument in the registration.
End-to-end registration, real filtering, real authentication and controller cache
behavior passed the bounded live probe. NetworkPolicy enforcement, Sonar deployment,
main analyses and actual Quality Gates remain separate required validations.

The fake API reproduces relevant namespace list denials and evaluates real HTTP
SelfSubjectAccessReview requests in strict mode. It does not exercise actual
authorization/admission, API discovery/filtering, TLS authentication, registration
selection, CNI, persistence, Sonar analysis or Quality Gates. Thus these results
reject the simple union, not every possible existing-Core architecture.

Sources: [installed cache implementation](https://github.com/argoproj/argo-cd/blob/e258ee23c3e52266d407572f4bcdfe7d9ed36cb5/gitops-engine/pkg/cache/cluster.go),
[controller cache keys](https://github.com/argoproj/argo-cd/blob/e258ee23c3e52266d407572f4bcdfe7d9ed36cb5/controller/cache/cache.go),
[cluster URL lookup](https://github.com/argoproj/argo-cd/blob/e258ee23c3e52266d407572f4bcdfe7d9ed36cb5/util/settings/cluster_informer.go),
[resource filter fields](https://github.com/argoproj/argo-cd/blob/e258ee23c3e52266d407572f4bcdfe7d9ed36cb5/util/settings/filtered_resource.go),
[credential selection](https://github.com/argoproj/argo-cd/blob/e258ee23c3e52266d407572f4bcdfe7d9ed36cb5/pkg/apis/application/v1alpha1/types.go),
[Argo cluster registration and RBAC behavior](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/).

## 構成と入口

[application.yaml](application.yaml) が管理入口、[registration.py](registration.py) が登録用公開設定の生成、[validate.py](validate.py) が静的検査を担当します。

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [cache-probe/](cache-probe) | 共有 cache の対象範囲を確認する試験用リソース。 |
| [chart/](chart) | 共有 SonarQube と DB・通信制限を描画する Chart。 |

### 本文にコメントを入れないファイル

JSON などの形式、埋め込み・checksum、既存 reader の文字コードの前提を保つため、役割はここで説明します。

| ファイル | 役割 |
| --- | --- |
| [access.yaml](access.yaml) | 既存 Argo CD Core が SonarQube を管理するためのアクセス範囲を定義します。 |
| [additional-inclusions.yaml](additional-inclusions.yaml) | Argo CD が SonarQube 管理で読み取るリソース種別を追加する設定です。 |
| [existing-sa-credential.sh](existing-sa-credential.sh) | 既存 ServiceAccount の資格情報を登録処理へ渡す補助スクリプトです。秘密の扱いは既存手順に従います。 |
