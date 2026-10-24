# platform-gitops

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
