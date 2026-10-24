# Initial proposal review and checks

Scope: read-only PLAT-196 inventory and uninstalled first-batch access proposal.
No actual migration, API/data acceptance or final PLAT-199 pass is claimed.

* Independent reviewer: GPT-6.1-Sol, same model requested by the user.
* The reviewer checked inventory/ownership identity equality, key object counts,
  PV policies, actual old Argo role facts, all four namespace cache read sets and
  whether pending checks were misrepresented as passing.
* P2 finding: workload write permission enables indirect Secret access and use of
  namespace ServiceAccounts. Corrected approval-scope.md to distinguish direct
  Secret API permissions from effective workload authority. This risk is part of
  the pending decision, not an assertion of credential isolation.
* Targeted static check passed: all 572 inventoried identities map to ownership
  rows, eight access documents parse, and the new role rules contain no delete,
  direct Secret, wildcard-resource or cluster-scoped grant.
* Live baseline: 32 Pods, 28 Running, all Running container statuses Ready;
  four completed migration Pods retained. pod-baseline.json captures UID and
  runtime imageID for later comparison.
* The source check found no AGENTS.md or .agents/skills/SKILL.md in the four
  checked-out repositories. README, CI validation entrypoints and relevant
  progressive-delivery/Sonar/cache/Account handoff instructions were read.
* No full suite, repeated Sonar/cache regression, SAST rebuild or live write was
  performed. Required CI, exact rendered/live immutable-field comparison, alias
  TLS/cache behavior and API/DB data acceptance remain pending.

## Follow-up preparation

The original fixed SHA c930c61 passed both push CI 40116893189 and PR CI
40116919773 (validate/ci-result success). These results cover that SHA only.

The new offline renderer uses the same repository-locked Helm 4.2.4 binary
checksum and vendored Istio chart. check_proposal.py passed for the proposed
bounded authority, namespace-restricted fail-closed webhooks, unchanged Rollouts
Deployment identity and persistent Account DB. This is an offline check, not
evidence of live readiness or permission sufficiency.

Corrected ownership source: mesh-demo fixture declarations belong to
platform-gitops/learning/istio/chart. Excluded controller-generated istio-ca-crl
from adoption and classified it as generated state. Also excluded argocd-cm from
the captured Core parents because the composed declaration has a single separate
owner. Final adoption capture has 53 non-Secret parents and 21 CRDs, not generated
Pods/status. Review of the new renderer/proposal is pending.

## Final wiring implementation

Source/role wiring is complete.21 affected adapter/receipt tests pass, including
old-context/session rejection and definition-before-workload sync without spec
patches. Historical fixture files remain unchanged; only synthetic in-memory
fixtures bind to the new target. Seven manual children have existing source
directories, unique managed identities, no managed Role/Secret declarations, and
Canary/Rolling definition renders produce the intended Git-owned exact SHA and
ignore set.

The two native operation-boundary objects passed server create dry-run and were
confirmed absent afterward. Actual enforcement/positive/negative authorization is
not yet tested. All21 same-spec CRDs passed server-side dry-run with
force-conflicts=false and argocd-controller manager; no ownership was saved.
Pinned-source inspection corrected the migration option to
ClientSideApplyMigration=false and the Rollouts plugin/lease namespace to its
unchanged Pod SA namespace. Argo SSA's built-in ForceConflicts is explicitly
disclosed for the one execution decision; no resource Force/Replace is set.

Prior heads also completed CI: platform daa3e77 push40118889127/PR40118893053
success, application-manifest8141756 PR40118894852 success. These are not claims
about the new commit. Final source/operation-boundary review and latest required
CI remain execution gates; no live grant, token, sync, admission activation or
data recreation has occurred.
