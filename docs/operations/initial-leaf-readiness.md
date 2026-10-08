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
