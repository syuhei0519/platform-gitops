# 障害対応・復旧手順

最初に、何が起きているかを読み取りで調べます。その後に必要な復旧操作を選び、操作が受理されたことと、配信・Git の両方が復旧したことを別々に確かめます。対象や結果が不明なまま次のリリースを始めないことが大切です。

## 確認と状態変更の境界

| 区分 | できること・意味 | 注意 |
| --- | --- | --- |
| 読み取り確認 | Git / PR / CI、Backstage の GET `state` / `history`、Argo と対象 resource の状態、監査記録を読む。 | 承認された対象の情報に限定し、秘密値を表示・転載しません。 |
| 状態変更：通常の復旧 | 前版へ戻す profile の PR、承認後のマージ、control → workload の同期。許可される状態での Abort。 | 操作の request ID、対象、差分、前提を保持します。 |
| 状態変更を含み得る照合 | POST `reconcile`。要求の種類に応じて結果を照合し、履歴を更新する。 | release / restore では2段目の workload 同期を開始することがあります。読み取り専用として扱いません。 |
| 状態変更：明示した例外 | 初期 bootstrap、Secret の管理、緊急復旧、受入試験で必要な再起動など。 | 対象・操作・権限を個別に承認し、Git との整合と後片付けを確認します。 |

通常入口は [配備契約](../../learning/existing-kind/DEPLOYMENT.md) です。手動 `kubectl apply`、image の直接変更、定例的な `rollout restart` は通常の配備・差分修復に使いません。bootstrap の承認は日常更新の承認へ引き継ぎません。Secret 値は Git 外で管理し、発行・保存・期限・権限は個別に扱います。緊急の Git 外操作も、実施後に Git の望む状態へ整合させます。

## 最初にそろえる読み取り情報

1. 対象 repo、PR、manifest commit、image digest、配備方式、観測時刻を確認する。
2. control / workload の両 Application を読む。比較 revision と、完了した同期の revision を区別する。
3. Rollout の generation、observedGeneration、pause / abort、AnalysisRun の理由を読む。
4. 対象 Pod の Ready、所有関係、image、同じ観測窓の配信と計測を確認する。
5. Backstage の履歴で、未確定の要求、既存の request ID、操作の監査結果を確認する。

生ログや実体の識別情報は承認された非公開の証拠に残し、公開する結論には秘密や環境識別値を含めません。readonly の収集でも、全 Secret の表示や認証情報を含む設定の丸ごと転記は必要ありません。

## 症状ごとの判断と復旧

以下の「復旧」はすべて **状態変更** です。実行できる対象・権限・差分を確認してから進めます。

| 症状 | 読み取り確認 | 判断 | 復旧 | 成功確認 |
| --- | --- | --- | --- | --- |
| error が増えた | AnalysisRun の availability 判定、対象 image / Pod、リクエスト数、実際の error を同じ窓で照合。 | 十分な有効データで閾値違反なら品質不合格。低 traffic や欠測と区別する。 | 自動 Abort の成否を確認し、必要なら許可された Abort。その後、前版 PR と二段階同期で Git も戻す。 | stable への実配信と正常応答、前版の Git / image、同期完了を確認。 |
| latency が増えた | 有効な latency 分布、250 ms 以下の割合、対象窓、依存先の状況を読む。 | 成功応答が多くても latency の条件を満たさなければ不合格。 | 配信復帰と前版への Git 復元。試験用遅延があれば承認された方法で解除。 | 同じ条件の新しい窓で error / latency と対象 image を確認。 |
| metrics が欠ける | series、収集経路、Collector / Prometheus の状態、時刻の新しさ、対象の対応関係を読む。 | 測れない状態は「正常」ではない。データ欠測と plugin 自体の Error も区別する。 | 計測基盤の所有 repo で原因を修正・復元して同期。配備は停止を保ち、新しい評価または Abort を判断。 | データが戻り、対象と窓が有効であること。古い AnalysisRun を成功扱いしない。 |
| traffic が少ない | 収集が正常で、評価対象 role の5分間の request 数が20未満か確認。 | 評価材料が足りない状態。欠測と原因は違うが、自動昇格の根拠にはならない。 | 閾値を下げず、承認された評価用 traffic と新しい評価窓を用意するか、停止・Abort を選ぶ。 | 必要な件数と品質条件を満たす新しい判定。 |
| Argo が同期しない・食い違う | main、PR の merge commit、control の比較と同期結果、workload の固定 revision と同期結果、進行中 operation を順に読む。 | 1段目待ち、2段目待ち、拒否、main の先行、別操作を切り分ける。`Synced` や比較 revision だけで完了としない。 | レビュー済み差分を再確認して正しい段階を同期。想定外の変更なら Git 修正 / 前版 PR へ戻る。 | 両段階の整合、workload の成功 revision、Pod・配信・計測の一致。 |
| token expiry / 認可エラー | 値を出さず期限とセッションの対象、拒否された操作、既存履歴を確認。 | 期限切れや権限不足で拒否されたのか、応答不明なのかを分ける。 | 新規発行や追加権限が必要なら、その操作について個別承認を得る。承認後も先に既存要求を照合。 | 許可範囲と期限が正しいこと、以前の操作結果が確定していること。 |
| timeout / 応答が返らない | 同じ request ID の履歴・監査、Argo / Rollout の操作結果を読む。 | client timeout は操作取消や失敗の証拠ではない。外部操作が成功している可能性がある。 | 下記の照合手順へ。新しい ID で重複送信せず、必要な照合と同一内容の再送だけを行う。 | 既存結果の確定、受理イベントと実操作が重複していないこと。 |

判定の具体値と結果の意味は [検証シナリオ](verification-scenarios.md#判定の読み方) を参照してください。収集が戻っただけで、停止した配備が自動的に合格へ変わるわけではありません。

## Abort と Git の前版復元を順に行う

Abort は、候補版の進行を止めて stable へ配信を戻すための runtime 操作です。Git の profile を前版へ戻す処理とは別です。Abort の応答が成功でも、実際の配信復帰は観測する必要があります。

1. **読み取り**：既存要求と対象実体を照合し、Abort が許される状態か確認する。
2. **状態変更**：必要な場合に許可された Abort を要求する。同じ request ID と payload を記録する。
3. **読み取り**：Rollout の abort 状態、stable / candidate の実配信、正常応答を確認する。Git に候補版が残っていれば恒久復旧は未完了。
4. **状態変更**：検査済みの前版へ戻す profile PR を作り、最終差分と CI をレビューする。承認後に main へマージし、control → workload を同期する。
5. **読み取り**：前版の Git revision、image、Rollout / Pod の実体、同期結果、配信・品質をそろえて確認する。
6. **状態変更・個別承認が必要な検証**：永続的な復旧の受入で再起動・再同期を試す場合は、対象と操作を明示して承認する。通常の更新手順に再起動を追加しない。
7. **読み取り**：再起動・再同期後も前版、正常配信、監視が維持されることを確認し、非回帰と一時権限撤去を完了してから復旧完了を判断する。

現在の復旧証拠の検証では、アプリ Pod に加え Rollouts controller と Argo application-controller の再起動前後も照合します。controller 自体を別物へ置き換えず、設定が維持され、Pod が入れ替わり、Ready に戻ったことを確認します。実 UID を公開教材へ書き込む必要はありません。詳細は [evidence_verifier.py](https://github.com/syuhei0519/application-manifest/blob/573b0a6712693e03807659da660cce2fcacff527/learning/account-release/evidence_verifier.py) の検査に対応します。

Git の前版復元は DB データの巻き戻しではありません。データの変更を伴う障害では、互換性・migration・復旧方針を別に判断し、DB / PVC を削除して合わせないでください。

## timeout の後に同じ要求を照合する

request ID は「同じ操作を追うための番号」です。実装は外部操作の前に受理を保存し、同じ利用者・同じ ID・同じ payload の再送には既存記録を返します。同じ ID で内容や利用者を変えると拒否します。

1. **読み取り**：client の表示だけで失敗と決めず、GET `history` / `state` と監査から既存 ID を探す。
2. **判断**：`confirmed` 等の確定結果か、`accepted` / `outcome-unknown` 等の未確定結果かを分ける。未確定の間は新しい操作を重ねない。
3. **状態変更を含み得る照合**：必要な `reconcile` を権限のある入口から実行する。runtime action の結果照合と、release / restore の二段階同期の継続は役割が異なる。
4. **状態変更 API への再送**：必要なら同じ利用者、同じ ID、同じ payload で再送する。既存結果を返すことを確認し、新規 ID に置き換えない。観測値が古くなった場合も内容を勝手に差し替えない。
5. **読み取り**：監査の受理イベント数、返った既存結果、実際の配信状態を照合する。確定できなければ不明のまま停止し、対象・操作・理由を記録する。

今回の共有結果では、0.1秒の client timeout 後に監査で Abort 成功を確認し、同じ ID / payload の再送は既存の `confirmed` を返しました。受理イベントは1件です。この結果は Abort の同一要求の試験であり、全 API や恒久復旧まで成功したことを示しません。

## 復旧完了の判断

「操作を受理」「配信が一時復帰」「Git が前版」「適用済み revision が一致」「再起動・再同期後も維持」「全体非回帰・後片付け完了」を段階ごとに記録します。どれかが未確認なら、その部分を未完了として残します。現在共有されている最終復旧・全体非回帰・cleanup は未完了です。

実装の入口： [operations.py](https://github.com/syuhei0519/application-manifest/blob/573b0a6712693e03807659da660cce2fcacff527/learning/account-release/operations.py)、[control_adapter.py](https://github.com/syuhei0519/application-manifest/blob/573b0a6712693e03807659da660cce2fcacff527/learning/account-release/control_adapter.py)、[Backstage の API](https://github.com/syuhei0519/application-manifest/blob/573b0a6712693e03807659da660cce2fcacff527/learning/account-release/backstage/packages/backend/src/plugin.ts)。
