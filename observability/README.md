# observability の見方

監視画面の正本。dashboard は Chart の配布用コピーと一致させます。

## 構成と入口

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [dashboards/](dashboards) | アプリと基盤の Grafana dashboard JSON。画面上だけの編集はここへ反映されません。 |

### 本文にコメントを入れないファイル

JSON などの形式、埋め込み・checksum、既存 reader の文字コードの前提を保つため、役割はここで説明します。

| ファイル | 役割 |
| --- | --- |
| [dashboards/application-overview.json](dashboards/application-overview.json) | アプリの稼働・通信・資源利用をまとめて見る Grafana dashboard の正本です。 |
| [dashboards/platform-overview.json](dashboards/platform-overview.json) | 基盤の稼働・資源利用をまとめて見る Grafana dashboard の正本です。 |
