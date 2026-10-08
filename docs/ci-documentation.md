# 文書だけの変更とCI

入口は `.gitlab-ci.yml`。GitLab 19.2以上の `rules:changes:regexp` を使用する。
通常のpush/MRで、変更パスが以下の文書だけなら `ci/includes/docs.yml` の
`docs-check` 1ジョブを実行する。

- リポジトリ直下の `README.md`、`CHANGELOG.md`、`CONTRIBUTING.md`
- `docs/` 以下の `.md` ファイル（大文字小文字を区別）

文書とコードの混在、未知のファイル、依存定義、CI、Helm/values、security設定、
`docs/` 以下のJSONやhelper、ソースコード内のコメント変更は通常CIへ進む。
文書のrenameは旧パスと新パスの両方が許可範囲にある場合だけ文書扱いとなる。
文書の削除も文書差分だが、削除済みファイルは現在内容の検査対象に含まない。
symlinkや制御文字を含むパスは軽量ジョブでも成功扱いにしない。

MRはGitLabのtargetとの差分、main以外のpushはdefault branchとの差分全体、
mainへのpushは直前commitとの差分を使う。コードを含むbranchへ文書だけ追加pushしても
通常CIのままになる。比較元がないmainの初回pushは通常CIへ倒す。

API/web/schedule等の明示起動は文書差分でも通常CIの定義を読む。
既存のworkflow、fork/tag制限、保護main・変数・実行元・手動ジョブの条件は維持するため、
明示起動しただけで全ジョブが起動可能になるわけではない。
パス判定は実行コストの制御であり、資格情報の権限制御ではない。

`ci/includes/full-pipeline.yml` は従来のジョブ定義をまとめて保持する。
依存する `needs` や成果物契約を一括で残し、必須の依存をoptionalにしていない。
通常CIの `security-policy-test` でも `ci/test-docs-check.sh` を実行する。

## 軽量ジョブの検査範囲

`ci/docs-check.sh` は差分のパスを再確認し、現在存在する変更Markdownだけを一時領域へコピーする。
Markdownの基本検査はfencedコード例の外に残ったGit競合マーカーを検出するもので、
Markdown全体の構文・リンク切れ・表記規則を保証するlinterではない。

secret検査には既存source-scanと同じdigest固定のTrivyを使う。
Markdownを除外する既定allowルールを解除し、固定の専用設定でsecretのみを検査する。
脆弱性DBの取得、依存インストール、build、render、SBOM生成、公開、manifest提案は行わない。
この成功は、コード全体・Git履歴・依存脆弱性・配備の安全性を保証しない。

secret検出とscanner異常はどちらもジョブを失敗させる。scannerの生ログ・JSONは
検出内容を含み得るため表示もartifact保存もせず、終了時に一時領域ごと削除する。
成功ログには検査した現在ファイル数だけを出す。削除だけならscannerを起動しない。
ジョブ上限5分、scanner上限2分は軽量処理の実行予算であり、性能測定に基づく保証値ではない。
shallow cloneに比較commitがなければ必要なcommitを取得し、取得できなければ失敗する。
main以外のpushではjob実行時にdefault branchを取得するため、その間にmainが進むと
パスの再確認が失敗することがある。その場合は最新mainへ追随して再検証する。

## ローカル確認

リポジトリ直下で `sh ci/test-docs-check.sh` を実行する。
実Git差分とfake scannerで、検出/異常時の非成功、混在・rename・削除・symlink、
比較元、ログ非表示、一時資源の後始末を検証する。実scannerの実行とは別の検証である。

CI設定を変更するときはGitLab CI Lintで条件付きincludeとneedsを検証し、
文書だけ・混在・未知パス・CI変更・明示起動の分岐を確認する。
実ジョブには `lab-k8s` タグを実行できるRunnerとGit/イメージ取得経路が必要。
