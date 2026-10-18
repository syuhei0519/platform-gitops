## Backlog metadata

- Key: `sre-sli-slo`
- Epic: `sre-observability`
- Type: `feature`
- Priority: `P1`
- Status: `backlog`
- Primary repository: `syuhei0519/platform-gitops`
- Repositories: `syuhei0519/platform-gitops`

初期状態はBacklogです。Readyへの昇格は依存・環境・Credential・必要な判断の確認後に行います。
複数repoの関連PRをこのIssueへ結合し、全体のAcceptance CriteriaとValidationで完了を判定します。

## Background

既存HTTP count・latency・error metricsを使えるためTrace完成を待つ必要がない。

## Goal

既存Metricsで評価可能なSLI・SLO・error budgetが定義される。

## Scope

- 対象ユーザー経路・分母と分子
- 目標・評価窓・除外条件
- query・dashboard・欠測方針

## Out of scope

- Distributed Trace必須化
- SLAの法的保証

## Acceptance criteria

- [ ] 可用性とlatencyのSLI query・目標・評価窓がversion管理される
- [ ] 既存metricsでSLIとerror budgetが算出できる
- [ ] trafficゼロ・欠測・低trafficの扱いが明示される

## Validation

- 既存metricsと合成fixtureでSLI計算結果を照合する
- error増加・trafficゼロ・欠測の各判定を確認する

<!-- backlog-dependencies:start -->
## Dependencies

Hard Dependencyの正本はGitHub Native blocked_by。詳細とSoft Dependenciesは下記の再編管理情報を参照。
なし。依存がないことはReadyを意味しない。

<!-- backlog-dependencies:end -->

## References

- https://github.com/syuhei0519/platform-gitops/tree/b989e29b8addbd6396c5c2b4833b969bc15fc8fa

<!-- backlog-key: sre-sli-slo -->

<!-- manager-capability-reorg-20261012:start -->
## 再編後の管理情報（2026-10-12）

- Backlog key: `sre-sli-slo`
- Level: Execution
- Purpose: Execution
- Epic: sre-observability
- Priority: P1
- Quality Level: Standard（元の目的・ACの範囲のみ）
- Status: Backlog / paused（未Ready・開発停止）

### 担当Scope

Define availability/latency SLI and SLO queries from existing HTTP counters/histograms including zero/missing/low-traffic semantics.

### Execution Acceptance Criteria

- [ ] 可用性とlatencyのSLI query・目標・評価窓がversion管理される
- [ ] 既存metricsでSLIとerror budgetが算出できる
- [ ] trafficゼロ・欠測・低trafficの扱いが明示される

### 主Validation Layer

- 既存metricsと合成fixtureでSLI計算結果を照合する
- error増加・trafficゼロ・欠測の各判定を確認する

## Hard Dependencies

GitHub Native blocked_byを正本とする。以下はその管理View。
- なし

## Soft Dependencies

- なし

## Unlocks

- [k6-quality-gate — syuhei0519/backend-app#17](https://github.com/syuhei0519/backend-app/issues/17)
- [rollout-metric-gate — syuhei0519/application-manifest#15](https://github.com/syuhei0519/application-manifest/issues/15)
- [sre-alerting — syuhei0519/platform-gitops#17](https://github.com/syuhei0519/platform-gitops/issues/17)

### 利用可能な既存Capability / 完了済み履歴

- refs: https://github.com/syuhei0519/platform-gitops/blob/482ba450983ac2929f8176374d0d6ca50a69cac6/charts/grafana/files/application-overview.json
reason: Existing account HTTP count/error/latency histogram queries provide SLI inputs without distributed-trace prerequisite.
status: implemented_baseline_not_feature_completion
- refs: https://github.com/syuhei0519/platform-gitops/blob/482ba450983ac2929f8176374d0d6ca50a69cac6/docs/collector-contract.md
reason: Existing Collector/New Relic baseline and historical controlled acceptance are documented; current credentials/account usage remain unverified.
status: implemented_baseline_not_feature_completion

### Ready判定で残る確認

- 明示的な開発再開指示待ち。環境・Credential・必要な判断/承認は未確認。依存ゼロはReadyを意味しない。
- Explicit user resumption; current development freeze remains in force.
- Confirmed execution environment, required tools and scoped access. No dependency-free issue is automatically Ready.

依存なしはReadyを意味しない。環境・Credential・必須設計・承認を未確認のままReadyにしない。既存Project所属・フィールドは変更しない。新規IssueのProject所属は未確認。

### 実行制約・履歴継承

1つの主変更と1つの主Validation Layerに限定する。既存Security / CI / Release・独立Test / Review・承認ゲートを維持し、AC証明に必要な最小限だけ検証・記録する。全Featureの検証や新しいHardeningを後付けしない。非Blockingの改善はFollow-upへ分離する。修復ループが2回を超える、またはScopeが拡大した場合は停止してManagerへ戻し、必要なDefect分割を行う。

既存PR・証跡・attempt / retry_of・失敗・承認・残予算を継承する。Issue分割は履歴のリセットや新しい実行予算ではない。Implementation / Test / Review / Merge / Publish / DeployとIssue Closeは今回行わず、明示的な再開指示を待つ。

<!-- manager-capability-reorg-20261012:end -->
