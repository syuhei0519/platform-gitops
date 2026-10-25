# platform-gitops

変更から配備までの流れ、障害時の復旧、検証の合否は [4 repository 共通の学習ガイド](docs/learning-guides/README.md) にまとめています。確認済みの結果と未完了部分、読み取りと状態変更を分けて説明します。

## Existing kind deployment (PLAT-195/198)

For `kind-learning-20261018-69c763`, the ordinary deployment entry is a reviewed
Git commit/PR followed by Argo CD sync. Follow the [current deployment contract](https://github.com/syuhei0519/platform-gitops/blob/main/learning/existing-kind/DEPLOYMENT.md).
Manual workload apply, image mutation and rollout restart from historical learning
instructions are retired. Existing Argo Core is reused; the old platform-lab and
isolated Account trial instructions are not the current cluster's deployment route.
This entry-point change does not claim that the pending PLAT-177/199 acceptance has passed.


kind、Argo CD、App of Apps、GitLab Runner foundationを所有するリポジトリです。

固定したkind/KubernetesとArgo CDを `bootstrap/` から導入し、`charts/root` のApp of AppsでPlatformとWorkloadを管理します。Chart既定値は `workloadsEnabled: false`、初回イメージ由来を検証済みの `environments/local/root.yaml` は `true` です。新規構築ではSecret再注入・イメージ準備の確認前にWorkloadを有効化しません。

`charts/runner-foundation` はRunner managerだけにbuild Namespace内の限定権限を与えます。公式Runner Chart 0.92.1の値は `environments/local/gitlab-runner.yaml` に固定し、認証値は既存Secret `gitlab-runner-auth` の `runner-token` だけを参照します。

秘密値とアプリChartはこのリポジトリで生成しません。Namespaceの唯一の定義元は `bootstrap/namespaces.yaml` で、運用者がbootstrap時に適用し、Argo管理Chartは生成しません。`sh ci/validate.sh` は有効化したchild topology、AppProject allowlist、削除保護、Runner/BuildKit Pod契約、version lockの一致を検証します。

Core Platform v1の設計正本・責任境界・稼働基準は [docs/bootstrap.md](docs/bootstrap.md) と [PE-001基準記録](docs/implementation/PE-001-baseline.md) を参照してください。固定版台帳は `bootstrap/versions.lock.yaml` です。

最終版の新規構築・観測・障害試験は [再構築と最終受入](docs/operations/final-rebuild.md) に従います。受入完了は実測索引と親Issueの状態を照合して判定します。

`charts/runner-foundation` と `charts.gitlab.io` の Runner は GitLab Runner の権限境界を学ぶために残しています。
アプリの CI は GitHub Actions で実行し、Runner 構成の保持は GitLab CI の再有効化を意味しません。

## 構成と入口

Argo CD の親子 Application、共有基盤、監視と権限の定義を管理します。

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [.github/](.github/README.md) | GitHub の CI、Issue、PR の入口。実行条件は workflows に置きます。 |
| [bootstrap/](bootstrap/README.md) | 基盤の初期構築に使う kind・Argo CD・namespace と固定版の定義。現在の対象は運用文書で確認します。 |
| [charts/](charts/README.md) | Kubernetes のリソースを生成する Helm Chart。環境ごとの差は values で渡します。 |
| [ci/](ci/README.md) | 検査の実行手順と境界テスト。GitHub から呼ぶ処理は github にまとめます。 |
| [docs/](docs/README.md) | 設計上の約束、検証結果、運用・引き継ぎの文書。過去の記録と現在の手順を区別して読みます。 |
| [environments/](environments) | 環境ごとの Chart 上書き値。local がローカル環境向けの入力です。 |
| [learning/](learning/README.md) | テーマ別の学習・検証用定義と補助ツール。実行前に各 README の対象と前提を確認します。 |
| [observability/](observability/README.md) | 監視画面の正本。dashboard は Chart の配布用コピーと一致させます。 |
| [security/](security/README.md) | 検査の合否条件と schema。アプリの機能設定とは分けて管理します。 |
| [tools/](tools/README.md) | CI から呼ぶ Go ツールと、共有ツールの利用手順。 |

### 本文にコメントを入れないファイル

JSON などの形式、埋め込み・checksum、既存 reader の文字コードの前提を保つため、役割はここで説明します。

| ファイル | 役割 |
| --- | --- |
| [renovate.json](renovate.json) | 依存と共有 CI の固定参照を更新する Bot の設定です。有効・無効の状態もここで指定します。 |

ファイルの説明範囲と除外理由は [棚卸し記録](docs/file-guide.md) を参照してください。
