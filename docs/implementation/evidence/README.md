# 過去の証跡

生成済みの JSON、ログ、重複 render は現行ツリーから整理した。過去の失敗・未実施を成功に変更していない。
以下の固定 commit の元ディレクトリから当時の出力を取得できる。Git 履歴は保持する。

[当時の出力](https://github.com/syuhei0519/platform-gitops/tree/b2e8758ce933a56bddf2931b167f2f5892e65358/docs/implementation/evidence)

整理対象: 1058 ファイル。新しい出力は CI artifact に保存し、再現コマンド・対象 commit・結論を Markdown に記す。

再現は repo root で `bash ci/github/test.sh` と `sh ci/validate.sh`。
runtime の観察は隣接する実装 Markdown を参照する。これらの静的チェックは実 cluster の再試験ではない。
PE019 の original-failures、attempt ごとの記録、最終監査も上記履歴に残る。

## 構成と入口

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [PE019/](PE019) | PE019 の受入時に確認した結果と未確認点の保存記録。 |
