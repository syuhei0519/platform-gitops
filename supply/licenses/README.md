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

## Consumer cutover and recovery

`supply/images.json` records a successful copy run, the verified destination
digest, and concrete normal-consumer files only for delivered images. CI
requires every recorded consumer to use the public GHCR tag at that exact
digest. Pending entries are intentionally absent from `deliveries`; their
upstream references remain in place.

These copies preserve the OCI bytes, so recovery is a Git revert of the
consumer change back to the inventory `source` reference at the same digest.
For an Argo-managed existing-kind resource, review the revert render and sync
only the named Application or resource; do not sync the already-drifted root.
No database or PVC rollback is implied by an image-repository-only revert.

Normal online startup pulls the fixed GHCR reference directly. `kind load
image-archive` is allowed only by the separately approved
`retained-oci-archive` offline exception in the fresh-profile supply lock. It
is not a normal delivery step and must not be used to claim GHCR availability.
The representative isolated check is a direct
`docker run --rm --network none <prometheus GHCR tag@digest> --version`; it
must report Prometheus 3.15.0 on linux/amd64 without a preceding archive load.

