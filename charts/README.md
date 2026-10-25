# charts の見方

Kubernetes のリソースを生成する Helm Chart。環境ごとの差は values で渡します。

## 構成と入口

各 Chart の Chart.yaml が識別情報、values.yaml が既定値、templates が出力を作る処理です。values.schema.json がある Chart では入力形式も検証します。

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [grafana/](grafana) | Grafana の dashboard 配布を描画する Chart。 |
| [observability-foundation/](observability-foundation) | 監視用 ServiceAccount と権限を描画する Chart。 |
| [otel-collector/](otel-collector) | OpenTelemetry の受信・転送を描画する Chart。 |
| [platform/](platform) | 基盤サービスを管理する子 Applicationを描画する Chart。 |
| [root/](root) | 基盤とアプリを束ねる親 Application と Projectを描画する Chart。 |
| [runner-foundation/](runner-foundation) | CI Runner の ServiceAccount と権限を描画する Chart。 |
| [workloads/](workloads) | Account と Order の子 Applicationを描画する Chart。 |

### 本文にコメントを入れないファイル

JSON などの形式、埋め込み・checksum、既存 reader の文字コードの前提を保つため、役割はここで説明します。

| ファイル | 役割 |
| --- | --- |
| [observability-foundation/values.schema.json](observability-foundation/values.schema.json) | 監視用 ServiceAccount と権限に渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [otel-collector/values.schema.json](otel-collector/values.schema.json) | OpenTelemetry の受信・転送に渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [platform/values.schema.json](platform/values.schema.json) | 基盤サービスを管理する子 Applicationに渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [root/values.schema.json](root/values.schema.json) | 基盤とアプリを束ねる親 Application と Projectに渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [runner-foundation/values.schema.json](runner-foundation/values.schema.json) | CI Runner の ServiceAccount と権限に渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [workloads/values.schema.json](workloads/values.schema.json) | Account と Order の子 Applicationに渡す設定値の必須項目・型・許可する値を検証する schema です。 |
