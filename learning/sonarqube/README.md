# PLAT-3 / PLAT-5: existing Argo CD cache boundary

This is a diagnostic prerequisite for the shared SonarQube rollout, not an
installation or a completed acceptance result. It never connects to Kubernetes,
creates credentials, changes the controller or modifies the existing learning
namespace permissions. The application SAST changes were merged separately in
backend-app#59 and frontend-app#14.

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

## Deployment remains stopped

The existing Core can still be reused, but a scope decision is required before
applying the proposed registration/inclusion additions. A different-URL and
authentication design may preserve both destinations, as described below. A separate bounded option is to
use the existing controller with **only** the Sonar destination while no existing
Application is registered. That changes the earlier learning destination scope and
requires explicit approval; the successful control case is not permission to do it.
Existing application Deployments, DBs, PVCs and Roles would not be deleted by such
a registration change, but future Argo management of the old destination would
remain unavailable until its scope is deliberately restored/redesigned.

Do not add old-namespace DB Secret read access, grant cluster-admin, deploy a second
Core, change the controller image, replace kind, or change the registration scope
as a workaround. Those are not authorized by the diagnostic test or this PR.
The Sonar server, NetworkPolicy live checks, sysctl change, seven credential flows,
main scans and actual Quality Gates remain separate unfinished steps.

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
 through an exec credential provider is a candidate; it has not been configured or
 tested against the real API here. No real token was read, copied, issued or stored
 for this investigation.
Do not introduce a new static Argo token, disable TLS verification, or claim this
candidate is deployable based only on these tests.

### Candidate authentication change requiring approval

`existing-sa-credential.sh` is a candidate exec-provider command for that distinct
URL, not an installed controller plugin. `credential_test.py` runs it in the exact
controller image, network disabled, with only a fake token mounted at the standard
projected-token path. It checks valid protocol/one-minute cache expiry and rejects
empty, invalid or missing input without credential output. Do not run it manually
against an existing Pod: its stdout is a credential protocol intended for client-go.

The proposed persistent change is a new `sonarqube-in-cluster` registration Secret
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
configuration even though it contains no token value. Approval is required before
creating that registration or using the provider with real credentials. No mounted
script, controller Pod change/restart, extra Core or Redis credential is proposed:
the reviewed script would be embedded as the command argument in the registration.
End-to-end registration, real filtering, real authentication and controller cache
behavior still require a bounded approved live validation before Sonar deployment.

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
