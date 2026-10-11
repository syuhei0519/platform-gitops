# PE-002A 初回の末端準備完了

親ApplicationのSynced/Healthy、子ApplicationのSynced/Healthy、migration成功、API動作は別の到達点。waveは跨Applicationのtransactionを保証しない。bootstrap/argocd-values.yamlのLuaは状態なし/OutOfSyncをProgressing、Degraded/失敗syncをDegradedとし、処理中のsyncをHealthyにしない。

bootstrap時に固定版Argo Helmへこのvaluesを渡す。既存Argo本体の更新適用はbootstrap所有者の操作として別に記録する（GitへのマージだけではArgo本体のvaluesは更新されない）。

初回image/Secret契約/配備定義が揃ってから、対象application-manifest mainの40桁SHAを記録する。tools/bootstrapで以下の読取専用待機を実行する。

```sh
go run . -context kind-platform-lab -revision <application-manifest-full-SHA> -timeout 10m
```

PostgreSQL→backend migration→backend API→frontendの順に判定し、先行末端も再確認する。backendは対象SHAのSucceededな全体syncとbackend-migration Sync hook成功記録を要求する。最新比較revisionがHealthyでも過去revisionのhook記録を流用しない。時間切れは非ゼロ終了し、次段へ進めない。toolはkubectl get Applicationだけで、Secretを読まず、sync/deployも実行しない。API/CRUDは別途HTTPで確認して保存する。

失敗時はJob終了状態と秘密値除去済みログを保存し、DB/接続を修復する。backend Application全体を**同revisionで明示sync**し、末端待機を再実行する。selective syncではmigration hookが実行されないので通常復旧に使わない。DB修復だけで自動再syncするとは想定しない。

10 health fixtureとrevision/hook拒否テストはtools/bootstrapでgo test -v ./...。隔離kindの初回順序/障害/復旧/CRUD試験はPE-006A再注入承認後に行い、AT-03はPE-002Bと一緒に受入する。現在のlabがHealthyであることは新規構築試験の代わりにならない。

参照: [Argo health](https://argo-cd.readthedocs.io/en/stable/operator-manual/health/)、[PE-002](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/2)。

## Orderの無効状態準備（P0）

`charts/workloads/values.yaml` と `environments/local/workloads.yaml` は
`orderEnabled: false`、`orderTargetRevision: ""` を保持する。この状態では既存の3Applicationのみを生成する。
Order用の架空SHAをlive値へ設定しない。`ci/fixtures/order-p0/enabled.yaml` はsynthetic sampleであり、render専用。
このfixtureの成功はManifestのchart実在、Git取得、実デプロイ、製品ACの合格を証明しない。

P1で別途承認を得て有効化する場合、採用済みapplication-manifestの小文字40桁commit SHAを
`orderTargetRevision` に指定する。空、main/tag、省略SHA、型違いはschemaで拒否する。
既存Accountの `targetRevision: main` は変更しない。Order DBは `lab-local-order-postgresql`（wave -10）、
Order APIは `lab-local-order`（wave 0）、既存の `account` namespace / `lab-account` AppProjectを使う。
同namespaceはセキュリティ分離の保証ではない。

observerの既定は既存3leafのみ。P1ではvaluesと一致する `-order-enabled` と `-order-revision` を明示する。
以下は手順のプレースホルダーであり、P0で実行するコマンドではない。

```sh
# tools/bootstrap: read-only observation, only after separate environment approval
# Account SHA is resolved from its main; Order SHA is the adopted fixed Manifest commit.
go run . -context <approved-context> -revision <account-main-full-SHA> \
  -order-enabled -order-revision <adopted-order-manifest-full-SHA> -timeout 10m
```

Orderの2leafは専用SHAのsource.targetRevision / status.sync.revision / operation syncResult.revision、
Synced/Healthy、operation Succeededをすべて要求する。DB StatefulSet / Order Deploymentの
Synced/Healthyなresource観測と、同revisionの `order-migration` Sync hook成功も確認する。
欠落・途中・失敗・未知状態は成功にしない。既存leafもoperation phaseが不明なら待機する。
毎周回、先行leafを再確認し、後段待機中のDB劣化を見逃さない。読取失敗・不足は有限待機後に非0終了する。
`-order-revision` だけの指定は無視せず引数エラーにする。有効化済みなのに無効のCLIを使う誤りは、
このCLIだけでは検出できないため、運用者がvaluesと引数を照合する。

このobserverはApplicationの非秘密JSONだけを読む。DB接続、schema履歴、Service endpoint、Pod imageID、
CRUDや永続性は推測しない。T6では独立Testと承認済み運用者が隔離kindでこれらを実測し、正規digestと照合する。
T7では正規統合後の実SHA・digestとAccount/Order回帰を再確認する。P0では両試験とも未実行。

P1前にはM2の正規digest・実マージSHA、独立T6、対象context、Secret metadata、PVC容量、backup/復旧責任者、
自動デプロイを含む承認が必要。rootがmain自動同期するため、有効化のmergeはデプロイ影響を持つ。
P0のnone提案はdefault/local render不変と、Namespace/AppProject/既存Account source非変更の証拠に限る。
マージ後の既存App状態不変の観測は後続担当が行う。

失敗時は後続merge・enable・デプロイを停止し、AccountデータとOrder PVCを保持する。
失敗Jobと非秘密statusを保存し、許可されたOrderのみの同期停止/無効化手順を使う。
初回には既知良好Order版がないため架空のrollback先を設定しない。修正forward migrationを独立再検証し、
Application全体を同期する。古いdigestへの復帰にもschema互換と証拠鮮度の確認が必要。

検証: `sh ci/validate.sh` は固定base由来の既存3Application snapshotとdefault/localのraw比較、
synthetic enabled renderと不正schema入力を検査する。CI render jobでは `TestOrderWorkloadsContract` が
semantic比較と2Appのwave/source/権限境界を確認する。Goの既存回帰とOrder observer異常境界は
`tools/bootstrap` の `go test ./...` で実行する。これらは実装者の自己検証であり独立Test/Reviewを代替しない。
