# docs の見方

設計上の約束、検証結果、運用・引き継ぎの文書。過去の記録と現在の手順を区別して読みます。

## 構成と入口

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [learning-guides/](learning-guides/README.md) | 4 repo をまたぐ変更の追跡、障害復旧、検証結果の読み方を学ぶ教材。 |
| [implementation/](implementation/README.md) | 実装時の判断、受入結果、未確認点を残す記録。 |
| [operations/](operations) | 適用・復旧・最終確認で守る運用上の手順と前提。 |

主なファイルの入口です。

| ファイル | 役割 |
| --- | --- |
| [ci-documentation.md](ci-documentation.md) | 現在の CI の入口、比較元の決め方、検査の再現手順。 |
| [github-migration.md](github-migration.md) | GitHub 移行時に決めた CI と認証・公開の運用記録。 |
| [learning-workflow.md](learning-workflow.md) | Issue・PR・検証記録に残す項目と、ログや生成物の保存方針。 |
