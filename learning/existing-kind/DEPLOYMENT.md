# Existing kind deployment contract

Target: `kind-learning-20261018-69c763`, existing Core in
`learning-progressive-control`. Never select a different cluster implicitly.

## Ordinary changes

1. Change parent declarations in the owning repository on a branch and open a PR.
   application-manifest owns service/API/DB/client declarations and Account profile;
   platform-gitops owns shared platform parents and root/child Applications.
   backend-app/frontend-app own source, checked images and release provenance.
2. Review the final diff and required CI at its fixed SHA, then merge to main.
3. Sync the appropriate existing Argo Application from reviewed main, resolving and
   recording the actual commit. Initial migration uses manual sync with pruning off.
   Account uses account-release-definition to declare the workload Application's
   fixed Git revision and exact ignore pointers, then syncs that workload revision.
4. Check Argo tracking/Sync/Health, readiness and affected data/metrics. On unexpected
   differences or a failed gate, stop the affected batch and diagnose. Recovery of
   desired state is a reviewed Git revert followed by Argo sync, without pruning.

Backstage proposes profile changes through reviewed Git PRs and requests bounded
Argo operations. It does not patch workload/Application desired-state spec, bypass
main review, use the deleted trial cluster, or mount an unrestricted kubeconfig.
Read-only kubectl inspection is permitted. Manual workload apply/set image/rollout
restart is not an ordinary release or drift-repair entrance. Generated Pods/RS,
Rollouts weights/status/subset hashes, serving leaf certificates and projected
credentials stay with their owning controllers, rather than being frozen in Git.

## Explicit exceptions

* Initial kind/CNI/Argo/root and reviewed security grants/cache registration are
  minimal operator bootstrap, with exact target/diff/authority recorded in Git.
  They do not authorize ordinary application updates by apply.
* Secret values remain outside Git. New issuance/storage/lifetime and additional
  security authority require the specific approval recorded for the task; historical
  SAST/token approval does not transfer. Secret-value updates alone do not rotate
  PostgreSQL roles. Record the actual manual rotation deadline and verification.
* Incident recovery outside Git requires the specific approved target/action and
  evidence, then reconciliation back to Git. No routine restart or ad-hoc image path.

No whole-root prune, Force/Replace, cascading finalizer, old DB/PV/Sonar deletion,
CA replacement, extra Core/controller or permission expansion is implied here.
Namespace/PVC/Secret protection, field ownership and render/live immutable-field
checks apply to each batch. See execution-approval.md and the separately approved
Istio follow-up for the exact bootstrap authority; permission changes stay explicit.

The old learning walkthroughs and completed handoffs retain historical evidence.
Their fixed-SHA originals are not a supported current deployment procedure. The
platform-lab topology is a separate historical target and must not be applied over
this existing cluster. Migration/acceptance status is recorded separately; this
contract does not declare PLAT-177/199 complete.
