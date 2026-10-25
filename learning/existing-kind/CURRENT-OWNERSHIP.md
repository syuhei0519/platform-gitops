# Existing-kind ownership contract

This document describes the repository contract and recreation sources. It does
not publish a live inventory, deployment status, object identities or measured
capacity. The initial capture is historical; current acceptance evidence is kept
locally and evaluated separately against the Jira acceptance criteria.

## Authoritative declarations

| Boundary | Authoritative Git source |
| --- | --- |
| Root/child Applications, shared platform parents and protected namespace metadata | platform-gitops `learning/existing-kind/root` and `managed` |
| Existing service/API/DB/PVC parents | application-manifest `learning/existing-kind/services` |
| Mesh fixture | platform-gitops `learning/existing-kind/adoption/mesh-fixture` |
| Reused Rollouts controller | platform-gitops `learning/existing-kind/managed/rollouts` |
| Isolated Account DB/PVC, client and one-time migration | application-manifest `learning/existing-kind/account-foundation` |
| Account Application source revision and mode-specific ignore pointers | application-manifest `learning/account-release/application-chart` |
| Account Rollout, routing, analysis and Services | application-manifest `learning/account-release/chart` plus reviewed runtime profile |
| Sonar and its security bootstrap | platform-gitops `learning/sonarqube`; retain the completed SAST evidence |

Ordinary introduction, update and recovery use reviewed Git changes followed by
Argo sync. Root and child declarations disable initial pruning and omit cascading
deletion. The detailed operation and exception contract is [DEPLOYMENT.md](DEPLOYMENT.md).

## Bootstrap and generated resources

Argo intentionally excludes CRDs from tracking annotations; the upstream
[tracking implementation](https://github.com/argoproj/argo-cd/blob/e258ee23c3e52266d407572f4bcdfe7d9ed36cb5/util/argo/resource_tracking.go#L279)
documents this behavior. Validate each declared CRD's schema, stable identity,
Application comparison and SSA ownership locally. Do not add a competing CRD
controller or custom annotation merely to make tracking appear present.

The kind local-path addon is minimum node bootstrap. Its recreation source is the bundled storage manifest in the pinned upstream
kind node image. Namespace
metadata can be protected by the foundation declaration while the provisioner,
StorageClass, service account, configuration and RBAC remain named bootstrap
exceptions. Read the pinned node source and inventory before recreation; do not
delete storage or treat this exception as an ordinary application deployment path.

Initial root/security setup is sourced by `bootstrap-root.yaml`, `render.py` and
the reviewed access proposals. Retained probe Application bootstrap requires its
approved reconstruction specification before recovery. Preservation of bootstrap
is distinct from adoption of a new Git desired-state definition.
Completed historical migration Jobs are retained records, not a reason to rerun
database initialization. Kubernetes/kubeadm/CNI defaults retain their upstream
bootstrap source. Explain any unmatched custom object before accepting coverage.

Pods, ReplicaSets, EndpointSlices, PV bindings, leader locks, public namespace CA
distribution and other generated resources belong to their actual parent or
controller. Do not freeze those runtime objects in Git. Rollouts exclusively owns
dynamic route weights, status and subset hashes; Git owns route structure, image,
profile, source revision and the exact mode-dependent ignore configuration.

## Local acceptance and recreation checks

1. Preserve a metadata-only inventory and correlate each object with its Git
   parent, generated owner or exact named bootstrap exception. Keep Secret values
   and private CA material outside Git and out of logs.
2. Render the declared source and compare immutable fields, selectors, storage,
   Secret references and ownership before the adoption batch. Use no Force,
   Replace, whole-root prune or delete/recreate shortcut.
3. Recreate ordinary parents from their reviewed owning Git source through Argo.
   Bootstrap source recovery, credential issuance and storage destruction retain
   their separately approved operating boundaries. Database rollback is not
   guaranteed by an application Git revert.
4. Verify readiness, real API/traffic, data retention and affected monitoring;
   record source/image revisions and the actual result locally. Include native
   sidecar/init ordering, existing missing limits and transient old/new Pod
   overlap in capacity calculations. Declared finite limits alone are not a
   whole-node hard ceiling or a measured peak guarantee.
5. Reuse completed required CI and prior capability evidence where applicable;
   complete the changed-path and end-to-end acceptance before marking Jira Done.
   A Healthy badge alone does not prove coverage or durable Git recovery.
