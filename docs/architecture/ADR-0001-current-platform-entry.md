# ADR-0001: Current Platform Lab entry and ownership boundary

- Status: accepted for the PLAT-201–217 remediation baseline
- Scope: the existing personal Platform Lab only
- Input revisions: backend `347369b2d68ae24e157bf107c3933aed5c2825c3`, frontend `aecccce46295714645387854a4dad37bdd0cd3c3`, application-manifest `9441c696fc6fece4b431b832b7d7099539210081`, platform-gitops `4a40c64a58105cc91fa1d48940707d09d003dcc6`

## Decision

There is one current entry: `learning/existing-kind/bootstrap-root.yaml`. It creates the protected `lab-learning-root` Application, whose only current child list is `learning/existing-kind/root/children.yaml`. The machine-readable companion is `learning/existing-kind/current-contract.json`; CI checks that file against both platform-gitops and application-manifest.

The current production-of-desired-state path is:

1. Current checked local-artifact/image identities and their runtime configuration are declared in the exact application-manifest paths named by `current-contract.json`.
2. A change is made through a reviewed Git PR in application-manifest or platform-gitops. An owner separately approves and merges it; no CI job merges or deploys it.
3. The root children consume `application-manifest/main` or `platform-gitops/main` at those exact repo/path pairs.
4. Argo CD reconciles the reviewed declarations. The portal may request a guarded sync/release operation; it is not an alternative source of desired state.

The protected-main image/release-record pipeline is related but is not yet a
current-path producer. At this decision's input revisions, its automated
proposal writes `environments/local/<service>.yaml`, which the current root does
not consume. PLAT-210 must first land the typed current-profile consumer and
protected verifier, then change the producer to propose only that reviewed
current target. Until both halves are present, publication remains separately
approved and the proposal connection is planned/conditional, not current.

No legacy bootstrap directory, old trial cluster, generated proposal directory, or evidence capture is a second current entry.

## Ownership classification

| Class | Owner and current examples | Change rule |
| --- | --- | --- |
| Mutable desired state | platform-gitops: `root/`, `managed/`, monitoring sources; application-manifest: `services/`, `account-foundation/`, Account release chart/profile | Reviewed Git change, paired contract CI, then normal Argo reconciliation |
| Frozen bootstrap | platform-gitops: captured CRDs/core inputs and completed bootstrap identities; application-manifest: completed migration Jobs after their first successful run | Preserve in place. Never regenerate or replay merely to make CI/render output match |
| Dynamic-field exceptions | webhook CA bundles, controller status/runtime fields, assigned Service/PVC fields, and the finite Argo ignore pointers in the current root | Controller-owned fields are compared by their explicit exception; no broad ignore or ownership transfer |
| Historical evidence | `adoption/`, `proposal/`, session receipts, acceptance documents, and Done PLAT-177/193–199 records | Read-only evidence. It cannot substitute for current desired state or fresh acceptance |
| Database declarations | Each service owns its database name/schema and runtime references in application-manifest; platform-gitops owns only the platform boundary and bootstrap orchestration | Runtime roles are DML-only for their owned service. Migration ownership and compatibility are handled by PLAT-209/214 |
| Job lifecycle | The declaring service owns its migration Job. Argo protection prevents prune/delete; after successful execution the concrete Job is frozen | A new reviewed version uses a new controlled migration contract. Do not mutate/recreate a completed Job |

PVCs and Secret values remain outside opportunistic regeneration. Current PVC immutable identities and the finite set of Secret references are checked by paired CI. Secret objects/values are not committed by this contract.

## Existing Done evidence versus fresh acceptance

Done PLAT-177 and PLAT-193–199 prove the retained cluster's observed 4-API/DB, Argo Core, Istio, Rollouts, Account release, monitoring, Sonar, two-SHA synchronization, failure modes, restart/resync, and cleanup behavior at their recorded revisions. They remain valid evidence for that existing environment.

They do **not** prove cacheless image retrieval, a new cluster's bootstrap ordering, fresh Secret input/recovery, finite profile/DB-role selection, interruption resume, or empty-child negative behavior. PLAT-212–217 must produce those measurements on the separately authorized fresh profile. A rendered manifest, this ADR, or an existing cluster's Healthy status is not fresh acceptance.

This decision moves no repository path, archives nothing, retires no cluster, changes no live RBAC/security policy, and authorizes no image publication or credential creation.
