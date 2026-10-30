# External image redistribution evidence

The files below accompany byte-preserving copies in
`ghcr.io/syuhei0519/platform-lab-images`. They do not replace or modify an
upstream image manifest. Each version directory binds the public license copy
and upstream evidence to the exact source digest in `supply/images.json`.

The public GHCR package is connected to this public repository so recipients
can follow its source link to these license materials without GitHub repository
authentication. Multi-architecture package descriptions are OCI annotations;
the mirror does not rewrite them because that would change the fixed digest.

Only inventory entries whose metadata says `redistributionReady: true` may be
dispatched with `redistribution-approved=true`.

The current evidence set covers Argo CD, Argo Rollouts, Istio pilot/proxy,
Prometheus, Prometheus config reloader, and the OpenTelemetry Collector. The
kube-state-metrics registry object remains unverified because its official
registry denied the read-only request; it is not redistribution-ready here.

Preparation records for kind node, Redis, PostgreSQL, GitLab Runner, and its
OCP helper retain confirmed upstream license/source material but deliberately
set `redistributionReady` to false. They are not listed in
`redistributionEvidence` and cannot be selected by the mirror write path until
their explicitly listed OCI, package notice/SBOM, and scan evidence is added.

