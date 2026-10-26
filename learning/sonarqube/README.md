# Shared SonarQube design and offline checks

This directory is a **public, synthetic design fixture**. It contains pinned
chart declarations, access-boundary proposals and offline tests. It does not
record a real cluster name, namespace inventory, Pod identity, token lifetime,
analysis result or live acceptance outcome.

Live target identity and observations belong in the operator's Git-ignored
runtime evidence. Their absence from Git is intentional and must not be treated
as proof that deployment, rollback, NetworkPolicy, TLS, analysis or cleanup
passed.

## Security boundary

The design reuses an existing Argo CD Core while preserving its existing
destination permissions. A SonarQube destination requires StatefulSet, PVC,
ConfigMap, ServiceAccount, Secret and NetworkPolicy management in its dedicated
namespace. It must not gain read access to unrelated application database
Secrets, cluster-admin, a second Core, a replacement controller image or a wider
registration scope.

Argo CD's resource cache is keyed by cluster server identity. Two registrations
for the same server URL do not form independent caches. The offline regression
uses the pinned Argo CD source, fake list/watch clients and a fake
SelfSubjectAccessReview API to demonstrate this constraint without a kubeconfig,
token or real cluster endpoint:

```bash
python3 learning/sonarqube/check_cache.py --argocd .build/argocd-istio
```

Passing the regression proves only the component behavior. It does not authorize
or prove a live registration, deployment or scanner acceptance.

## Pinned deployment inputs

`chart/` embeds the reviewed SonarSource chart and fixed image references. The
wrapper declares a dedicated PostgreSQL instance, TLS sidecar, PVCs and
NetworkPolicies. It contains no credential value. Render and validate it with
the CI-pinned Helm and `validate.py <rendered.yaml>`.

Credential values and private keys must remain outside Git, rendered artifacts,
CLI arguments and logs. A public certificate may be retained only for explicit
hostname, expiry and fingerprint verification. Password or token issuance,
storage, rotation and revocation are separately approved runtime actions.

`access.yaml` and `additional-inclusions.yaml` are proposals, not live evidence.
Any registration must use a TLS-verified endpoint, bounded namespaces and
`clusterResources=false`. Do not add a static Argo token or disable certificate
verification.

## Runtime evidence contract

When a live run is separately approved, record its target and redacted results
outside the repository, for example under a Git-ignored
`learning/sonarqube/.runtime/` directory. The record must distinguish:

- target and preflight identity;
- reviewed source and rendered-content hashes;
- readiness and storage observations;
- NetworkPolicy and TLS results;
- analysis and Quality Gate results;
- temporary-resource and credential cleanup.

Missing runtime evidence fails closed. Public examples and offline fixtures are
always synthetic and must never be promoted to a live-success claim.
