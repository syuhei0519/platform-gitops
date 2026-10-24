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
applying the proposed registration/inclusion additions. One bounded option is to
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

Sources: [installed cache implementation](https://github.com/argoproj/argo-cd/blob/e258ee23c3e52266d407572f4bcdfe7d9ed36cb5/gitops-engine/pkg/cache/cluster.go),
[Argo cluster registration and RBAC behavior](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/).
