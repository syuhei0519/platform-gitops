# security の見方

検査の合否条件と schema。アプリの機能設定とは分けて管理します。

## 構成と入口


### 本文にコメントを入れないファイル

JSON などの形式、埋め込み・checksum、既存 reader の文字コードの前提を保つため、役割はここで説明します。

| ファイル | 役割 |
| --- | --- |
| [scan-policy.json](scan-policy.json) | 共有 scan-gate が使うセキュリティ検査の合否条件です。 |
| [scan-policy.schema.json](scan-policy.schema.json) | 検査方針の JSON に必要な項目と許される値を定義します。 |
