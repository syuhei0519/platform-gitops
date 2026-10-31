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

The current ready evidence set covers Argo CD, its Redis image, Argo Rollouts,
Istio pilot/proxy, Prometheus, Prometheus config reloader, and the OpenTelemetry
Collector. Grafana 13.2.5 and kube-state-metrics v2.20.0 have verified OCI
identity and passing scan records, but remain preparation-only until their
listed redistribution materials are complete.

Preparation records for kind node, PostgreSQL, GitLab Runner, and its
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

For the delivered consumers recorded above, an online start can pull the fixed
GHCR reference directly. The representative isolated check is a direct
`docker run --rm --network none <prometheus GHCR tag@digest> --version`; it
must report Prometheus 3.15.0 on linux/amd64 without a preceding archive load.

This does **not** yet make the full fresh-profile startup registry-only. Its
current mixed supply mode still requires the retained Account archive, and the
approved live adapter can therefore run `kind load image-archive`. Completing
PLAT-229 still requires a separately reviewed normal registry supply mode that
generates no archive import step, while retaining archive import as an explicit
offline exception. The representative Prometheus check proves only that one
delivered public image can be pulled and started without manual loading; it is
not a fresh-kind or whole-profile acceptance result.

