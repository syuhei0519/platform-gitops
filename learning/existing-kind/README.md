# Existing-kind GitOps control contract

This directory defines the ownership and operating contract for adopting an
existing learning cluster into reviewed Git and Argo CD. It is not a live
inventory or an acceptance-status report. Runtime identities, endpoints, capacity
measurements and detailed acceptance evidence stay in the local execution record.

## Sources and operation

* [Ownership contract](CURRENT-OWNERSHIP.md): authoritative repository sources,
  generated resources, bootstrap boundaries and recreation checks.
* [Deployment contract](DEPLOYMENT.md): ordinary Git review and Argo sync, safe
  recovery and the explicit limits on bootstrap and incident exceptions.
* `root` and `managed`: Application relationships and platform parent declarations.
* `render.py`, `bootstrap-root.yaml` and reviewed access proposals: preparation of
  the minimum operator bootstrap; preparation does not authorize live execution.

Application/API/DB parents belong to application-manifest; shared platform parents
and Application relationships belong to platform-gitops. Source code and checked
image provenance remain in backend-app and frontend-app. Do not apply a separate
lab topology over an existing cluster or introduce a second controller.

Ordinary introduction, update and desired-state recovery use a reviewed Git change
followed by Argo sync. Read-only diagnosis is permitted. Generated Pods, route
weights, status and other controller fields retain their actual runtime owner.
Manual workload apply, direct image changes and routine rollout restart are not
alternative ordinary deployment entrances.

## Acceptance boundary

Keep initial prune disabled, avoid Force/Replace and cascading deletion, protect
storage and namespaces, and compare immutable fields and ownership before each
adoption batch. Credentials and private CA material remain outside Git. Issuance,
additional authority and destructive recovery retain their specific approval
requirements; completed SAST evidence does not transfer credential authorization.

Verify actual API/traffic, data continuity, affected monitoring, bounded drift and
durable Git restoration, using fixed reviewed sources and the changed-path CI.
Keep passed, pending and constrained acceptance points distinct. A Healthy badge
or preserved historical record alone does not establish final acceptance, and a
manifest revert does not guarantee database rollback.

Historical captures are retained as historical evidence, never as a substitute
for the current local assessment. Final Jira acceptance remains a separate
decision after the required runtime cases and cleanup.

## 構成と入口

現在の所有範囲と配備入口は、この README 冒頭の案内を優先します。採取記録や過去の提案をそのまま現在の操作手順として使いません。

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [adoption/](adoption) | 既存の親リソースから採取した固定宣言と識別情報。手書きの正本とは区別します。 |
| [inputs/](inputs) | 提案生成に使う採取済みの固定入力。環境情報を新しい説明へ転記しません。 |
| [managed/](managed) | GitOps が管理する共有基盤の生成済み親宣言。 |
| [proposal/](proposal) | render.py が作るレビュー用の構成と checksum。直接の手修正は避けます。 |
| [root/](root) | 子 Application を束ねる生成済み定義。 |
