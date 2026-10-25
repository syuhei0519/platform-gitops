# learning の見方

テーマ別の学習・検証用定義と補助ツール。実行前に各 README の対象と前提を確認します。

## 構成と入口

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [existing-kind/](existing-kind/README.md) | 既存環境の所有範囲と GitOps 入口、引き継ぎ時の提案・記録。 |
| [istio/](istio/README.md) | 通信経路、sidecar、段階配備を学ぶための定義と静的検証。 |
| [monitoring/](monitoring/README.md) | Account の計測収集、表示、SLI、通知の定義と静的検証。 |
| [progressive-delivery/](progressive-delivery/README.md) | Rollouts の固定版・権限・状態判定と Account Application の提案生成。 |
| [sonarqube/](sonarqube/README.md) | 共有 SonarQube の定義、既存 Argo CD への登録提案、権限境界の検証。 |
