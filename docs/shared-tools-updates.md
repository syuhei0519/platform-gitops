# 固定参照の更新

共有処理は backend-app の `.github/actions/shared-tools/action.yml` に置く。
`uses: syuhei0519/backend-app/.github/actions/shared-tools@<40桁SHA> # main` と必要な module 名を宣言する。
同じ repo の複数箇所は Renovate の `shared-ci-tools` グループで一緒に更新できる。
新しい Action を使う変更は、提供元を先に通常の merge で取り込んでから利用側を取り込む。

`renovate.json` は `enabled: false`。既存 App / token / 権限は変更せず、Bot や実行予定を追加しない。
設定を有効にした実 Bot の更新 PR 作成・実運用は未検証。platform #10 / #61 の完了を意味しない。

## 最小検証

Renovate 44.167.1 の公式 config-validator を `--strict` で実行し、設定の構文を確認した。
公式の github-actions extractor をネットワークなしで呼び出し、次を確認した。

- 固定 SHA と `# main`: SHA、main、github-digest の更新対象として抽出する。
- コメントのない SHA: unversioned-reference として更新対象外にする。
- 自 repo 内の `uses: ./...` は外部依存にせず、他の Action を共有ツールと誤認しない。

この環境では optional RE2 binding がなく標準 RegExp に fallback する。独自 regex manager は使用していない。
3 repo の workflow を一時ディレクトリへ複製し、そのコピーだけ enabled=true として
`renovate --platform=local --dry-run=extract --onboarding=false --require-config=ignored` も成功した。
実 repo の設定は無効のまま。確認は設定検証と読み取り専用の依存抽出までであり、実 Bot の動作確認ではない。

[Renovate公式仕様](https://docs.renovatebot.com/modules/manager/github-actions/#digest-pinning-and-updating) は、
裸の SHA が既定で更新対象外になることと、branch / tag コメントによる更新を説明している。

共有元を checkout したディレクトリでの再現コマンド（パッケージ取得だけはネットワークが必要）：

```sh
npm install --prefix /tmp/renovate-check --cache /tmp/renovate-cache --no-audit --no-fund renovate@44.167.1
/tmp/renovate-check/node_modules/.bin/renovate-config-validator --strict /path/to/repo/renovate.json
RENOVATE_PACKAGE_DIR=/tmp/renovate-check/node_modules/renovate node ci/test-renovate-extract.mjs /path/to/repo/.github/workflows/ci.yml
```
