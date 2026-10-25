# .github の見方

GitHub の CI、Issue、PR の入口。実行条件は workflows に置きます。

## 構成と入口

通常の検査は [workflows/ci.yml](workflows/ci.yml) から追います。

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [ISSUE_TEMPLATE/](ISSUE_TEMPLATE) | 目的・作業・結果・学びを記録する Issue のひな形。 |
| [workflows/](workflows) | イベントごとの CI 定義。ci.yml が通常の変更検査の入口です。 |
