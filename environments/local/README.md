# environments/local の見方

この階層の検証用ファイルと読み方をまとめます。

## 構成と入口

同じ名前の Chart に対応する YAML を上書き値として渡します。ここにあるだけでは配備は実行されません。


主なファイルの入口です。

| ファイル | 役割 |
| --- | --- |
| [grafana.yaml](grafana.yaml) | ローカル環境でGrafana の dashboard と接続に使う、Chart の上書き設定です。 |
| [observability-foundation.yaml](observability-foundation.yaml) | ローカル環境で監視に必要な実行主体と権限に使う、Chart の上書き設定です。 |
| [platform.yaml](platform.yaml) | ローカル環境で基盤サービスを管理する Applicationに使う、Chart の上書き設定です。 |
| [root.yaml](root.yaml) | ローカル環境で親 Application と Projectに使う、Chart の上書き設定です。 |
| [runner-foundation.yaml](runner-foundation.yaml) | ローカル環境でCI Runner の実行主体と権限に使う、Chart の上書き設定です。 |
| [workloads.yaml](workloads.yaml) | ローカル環境でAccount・Order の子 Applicationに使う、Chart の上書き設定です。 |
