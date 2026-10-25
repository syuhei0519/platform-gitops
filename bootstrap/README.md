# bootstrap の見方

基盤の初期構築に使う kind・Argo CD・namespace と固定版の定義。現在の対象は運用文書で確認します。

## 構成と入口


主なファイルの入口です。

| ファイル | 役割 |
| --- | --- |
| [argocd-values.yaml](argocd-values.yaml) | 初期構築時の Argo CD 設定。子 Application の状態判定もここで定めます。 |
| [kind.yaml](kind.yaml) | 初期構築用 kind のノード構成。 |
| [namespaces.yaml](namespaces.yaml) | 初期構築で必要な namespace の定義。 |
| [root-application.yaml](root-application.yaml) | GitOps の親 Chart を参照する最上位 Application。 |
| [root-project.yaml](root-project.yaml) | 最上位 Application に許可する repository と配備先の範囲。 |
| [versions.lock.yaml](versions.lock.yaml) | 初期構築に使うツール・イメージ・Chart の版と checksum を固定する入力。 |
