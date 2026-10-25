# 検証シナリオと結果の読み方

ここでは「何を変え、何を見て、どの結果なら合格か」をそろえます。以下は試験計画と既存結果の読み方であり、この文書作成で実機試験を再実行したものではありません。将来実行する場合も、状態変更と負荷には対象・範囲・復旧方法の承認が必要です。

## 全シナリオに共通する前提

- [現在の配備契約](../../learning/existing-kind/DEPLOYMENT.md) の対象と権限を確認し、別のリリースや未確定要求と重ねない。
- 前版と候補版の検査済み image、Git revision、配備方式、必要な計測経路を確認する。古い artifact の保存期限や検査の有効性も確認する。
- PR の最終差分と CI を確認し、状態変更は Git PR → control → workload の順で行う。モード切替は旧 image のまま必要な段階をレビューし、その後に候補 image へ進む。
- 同じ対象・同じ時間窓で Argo、Rollout、Pod、AnalysisRun、利用者のリクエスト、Envoy の記録を照合する。
- 結果には「期待」と「観測した事実」を分けて書く。公開文書には秘密値、実 UID / IP、内部絶対パス、実測容量を載せない。

## 判定の読み方

現在の [metric plugin](https://github.com/syuhei0519/application-manifest/blob/573b0a6712693e03807659da660cce2fcacff527/learning/account-release/metric-plugin/main.go) は5分間の窓を使います。評価対象 role ごとに request 数20以上、availability 99%以上、250 ms 以下の応答割合95%以上を要求します。query timeout は5秒です。対象の対応関係、計測窓の有効性・欠測なども検査するため、数値が良く見えるだけでは合格になりません。

| 結果・状態 | 意味 | 次に確かめること |
| --- | --- | --- |
| Successful / passed | 必要な有効データで条件を満たした。 | 配備方式に応じて次段へ進むか、手動待機するか。 |
| Failed | 有効なデータに品質条件の違反がある。 | 自動 Abort と実配信の復帰。別 role の欠測があっても、確認できた違反を打ち消しません。 |
| Inconclusive | traffic 不足や欠測などで、合格を判断できない。 | 理由、停止、昇格していないこと。対象データが戻っただけでは旧判定は成功になりません。 |
| Error | plugin / RPC など評価の実行に失敗した。 | データ不足とは別に、実行エラーと配備側の停止・Abort を確認。 |
| `CanaryPauseStep` | manual Canary が人の確認を待っている。 | 評価成功と対象の一致を確認してから許可された Promote を判断。 |
| `InconclusiveAnalysisRun` | 判定できないための停止。 | 通常の手動待機と混同しない。Promote で飛び越えない。 |

10% / 50% 段階では stable と candidate、100% 段階では candidate を評価します。100% で配信されない stable に traffic がないことは、評価対象の欠測と同一ではありません。[AnalysisTemplate](https://github.com/syuhei0519/application-manifest/blob/573b0a6712693e03807659da660cce2fcacff527/learning/account-release/chart/templates/analysis.yaml) は初回待ち300秒、判定回数1、failure / inconclusive / consecutive error の許容値0です。無限再試行や自動的な合格への読み替えはしません。

### 配信比率は実際のリクエストで確認する

設定 weight は「狙った比率」です。Pod の数は「用意した実体の数」です。どちらも利用者が実際に受け取った比率そのものではありません。

同じ観測窓の client 結果と Envoy のカウンター差分を、stable / candidate の対象実体と結び付けて読みます。有限件数では理想比率からずれるため、既存の検証条件に沿って判定します。unknown な応答、カウンターの reset、欠測、途中の対象変更を無視して割合だけ計算しないでください。閾値を都合よく緩めず、観測が成立しない場合は未確認として残します。

## 1. 正常な Rolling

| 項目 | 内容 |
| --- | --- |
| 前提 | 共通前提を満たす。Rolling と `promotion_mode: null` の組み合わせ、検査済みの前版・候補版。 |
| 目的 | Canary 用の操作を使わず、新版への置換と正常配信を確認する。 |
| 操作 | **状態変更**：レビュー済み profile PR をマージし、二段階同期を行う。方式切替が必要なら専用の段階を先に完了する。 |
| 観測点 | Application の完了した同期 revision、Rollout の observedGeneration / Ready / Available、Pod の image、API の正常応答。 |
| 期待結果 | すべての必要な replica が新版で Ready になり、実配信が正常。Rolling に Canary の steps / trafficRouting / analysis や Promote 操作を持ち込まない。 |
| 不合格時の対応 | 進行期限超過、Ready 不足、image 不一致を切り分ける。必要なら前版 PR と同期で戻す。良い metrics だけで Ready 不足を合格にしない。 |
| 後片付け | 試験入力・観測用の一時設定を復元し、Git と稼働状態の一致を確認する。 |

Rolling でも基礎となる resource はこの実装では Rollout です。Kubernetes Deployment へ自動的に切り替わる、という説明ではありません。[配備方式の実装](https://github.com/syuhei0519/application-manifest/blob/573b0a6712693e03807659da660cce2fcacff527/learning/account-release/release_modes.py) を参照してください。

## 2. manual Canary

| 項目 | 内容 |
| --- | --- |
| 前提 | Canary / manual、計測 plugin と観測経路が有効。各段階の評価に必要な traffic がある。 |
| 目的 | 判定成功後に人の確認を待ち、承認した段階だけ進むことを確認する。 |
| 操作 | **状態変更**：PR と二段階同期で開始し、10% / 50% / 100% の各評価と手動待機を確認して許可された Promote を行う。 |
| 観測点 | 各 AnalysisRun、pause reason、対象の generation、実配信比率、Promote の request ID / 監査。 |
| 期待結果 | 評価が成功しても手動待機が続く。`CanaryPauseStep` だけで停止中の正しい対象に限って Promote が許される。 |
| 不合格時の対応 | 欠測や失敗による停止を手動待機と読み替えない。対象が変わった古い要求は再確認し、必要なら Abort と Git 復元へ進む。 |
| 後片付け | 最終配信と Git の版を確認し、未確定要求・試験入力を残さない。 |

## 3. auto Canary

| 項目 | 内容 |
| --- | --- |
| 前提 | Canary / auto、承認された controller metric plugin、各評価対象の有効データ。 |
| 目的 | 各品質判定を通った場合だけ、人の Promote なしで進むことを確認する。 |
| 操作 | **状態変更**：PR と二段階同期で開始する。手動 Promote で進行を補わない。 |
| 観測点 | 10% / 50% / 100% の順序、各 AnalysisRun、実配信、最終 image と Ready。 |
| 期待結果 | 各評価の成功後に次段へ進み、最終的に候補版が stable になる。auto だから常に成功する、という意味ではない。 |
| 不合格時の対応 | Failed / Error / Inconclusive の理由を区別して下記の異常系へ。 |
| 後片付け | 最終状態の証拠を残し、試験用 traffic と一時設定を片付ける。 |

## 4. error による自動 Abort

| 項目 | 内容 |
| --- | --- |
| 前提 | auto Canary の評価中。隔離された承認済み試験入力を使い、前版の正常性と復元方法を確認済み。 |
| 目的 | 有効なデータで error が閾値を超えたとき、候補版の進行を止めることを確認する。 |
| 操作 | **状態変更・負荷あり**：承認された error 注入を候補側へ行う。収集経路は正常に保つ。 |
| 観測点 | 十分な request 数、availability の違反理由、AnalysisRun Failed、Rollout の Abort、stable への実配信。 |
| 期待結果 | 自動昇格せず Abort し、配信復帰を実測できる。Abort のフラグだけで復旧完了としない。 |
| 不合格時の対応 | 対象、窓、収集、試験入力を切り分け、影響を止める。保護条件を緩めない。 |
| 後片付け | 注入を解除し、前版 PR → 二段階同期 → 恒久復旧の確認へ進む。 |

## 5. latency による自動 Abort

| 項目 | 内容 |
| --- | --- |
| 前提 | error 試験と同じ安全な対象。latency を独立して評価できる正常な計測と十分な request 数。 |
| 目的 | 応答が成功していても遅すぎる場合に進行を止めることを確認する。 |
| 操作 | **状態変更・負荷あり**：承認された遅延入力を与える。 |
| 観測点 | 250 ms 以下の割合、availability、判定理由、Failed / Abort と実配信復帰。 |
| 期待結果 | latency 条件の違反が判定に現れ、自動昇格しない。error の違反だけで本ケースを合格にしない。 |
| 不合格時の対応 | 遅延入力・計測単位・対象・窓を確認し、進行を停止する。 |
| 後片付け | 遅延入力を解除し、前版の Git 復元と正常配信を確認する。 |

## 6. 低 traffic

| 項目 | 内容 |
| --- | --- |
| 前提 | 収集経路と対象 series が正常で、評価対象 role の request 数だけが不足する。 |
| 目的 | 少ない成功応答から誤って合格としないことを確認する。 |
| 操作 | **状態変更・試験条件の変更**：承認範囲で評価 traffic を減らし、5分の窓を観測する。 |
| 観測点 | request 数20未満、収集の健全性、`insufficient-traffic` の理由、停止と自動昇格の有無。 |
| 期待結果 | Inconclusive となり自動昇格しない。収集停止を低 traffic の証拠に使わない。 |
| 不合格時の対応 | 欠測ケースとの混在を調べる。閾値を下げて合格にしない。 |
| 後片付け | 承認された traffic 条件へ戻し、新しい有効な評価または Abort / 復元を選ぶ。 |

## 7. metrics 欠測

| 項目 | 内容 |
| --- | --- |
| 前提 | 欠測させる対象と影響範囲、計測基盤の Git 復元方法が個別に承認されている。 |
| 目的 | データを取れない状態で自動昇格しないことを確認する。 |
| 操作 | **状態変更**：承認された収集経路の障害を再現する。通常の変更入口を守り、無断で Collector を停止しない。 |
| 観測点 | 欠測・鮮度・対象の理由、AnalysisRun Inconclusive、Rollout の `InconclusiveAnalysisRun`、配信の進行。 |
| 期待結果 | 自動昇格せず停止する。plugin 実行 Error や品質違反とは分けて記録する。 |
| 不合格時の対応 | 別 role に有効な品質違反がないか、欠測条件が成立しているか確認する。単なる「止まった」だけでは原因一致を判断しない。 |
| 後片付け | Collector 等を所有する Git 定義へ戻し同期を確認する。データ復帰後も旧 AnalysisRun は成功に書き換えず、新評価または Abort を判断する。 |

## 8. timeout 後の同一 request ID の照合・再送

| 項目 | 内容 |
| --- | --- |
| 前提 | 許可された Abort 対象と未使用の request ID、同じ payload と利用者を保持できる。監査で実行回数を確認できる。 |
| 目的 | 応答を受け取れなくても、操作を二重に実行せず結果を回収できることを確認する。 |
| 操作 | **状態変更 API**：client timeout を0.1秒にした Abort 要求の後、履歴と監査を読み、必要な照合を行い、同じ ID / payload / 利用者で再送する。 |
| 観測点 | client timeout、監査の Abort 結果、返った既存 `confirmed`、受理イベント数。timeout を server 側の中断と見なさない。 |
| 期待結果 | 既存結果を返し、受理イベントは1件。新 ID で別操作を起こさない。 |
| 不合格時の対応 | 結果が不明なら未確定のまま次の操作を止める。異なる payload への差し替えや履歴の削除で通さない。 |
| 後片付け | 既存操作を確定させ、Abort 後の Git 復元へ進む。試験用 timeout を通常設定へ戻す。 |

release / restore の `reconcile` は2段目の同期を開始する場合があります。詳細は [timeout の照合手順](incident-recovery.md#timeout-の後に同じ要求を照合する) を参照してください。

## 9. Git に残る前版復元（durable restore）

| 項目 | 内容 |
| --- | --- |
| 前提 | 復元する検査済み前版、対象の要求履歴、現在の Git / runtime、必要な互換性を確認済み。 |
| 目的 | 一時的な Abort に加え、次の同期でも前版が保たれる Git の状態へ戻す。 |
| 操作 | **状態変更**：前版 profile の PR をレビュー・マージし、control → workload を同期する。 |
| 観測点 | PR / merge SHA、両 Application、完了した workload sync revision、前版 image、Pod、stable への実配信と品質。 |
| 期待結果 | Git の望む版と実配信が一致する。PR のマージだけ、または Abort だけで `restored` としない。 |
| 不合格時の対応 | Git、同期、runtime、証拠不足を切り分ける。DB データを Git の復元と同じものとして巻き戻さない。 |
| 後片付け | 次の再起動・再同期確認、全体非回帰、権限撤去まで未完了部分を引き継ぐ。 |

## 10. restart / resync 後も復元が保たれる

| 項目 | 内容 |
| --- | --- |
| 前提 | 前版の Git 復元と正常配信を確認済み。アプリ・controller の再起動という例外操作の対象、影響、権限を個別承認済み。 |
| 目的 | プロセスの一時状態に頼らず、Git の版と正常配信が再構成されることを確認する。 |
| 操作 | **状態変更・例外の検証**：承認されたアプリ Pod、Rollouts controller、Argo application-controller の再起動と、対象 revision の再同期を行う。通常更新に `rollout restart` を追加する手順ではない。 |
| 観測点 | 前後の対象対応、controller 設定の維持と Ready、Pod の入れ替わり、同期完了時刻と revision、新しい観測窓の配信・品質。 |
| 期待結果 | Git / image / 所有する resource が維持され、新しい Pod と controller が Ready。再同期後も前版への配信が保たれる。 |
| 不合格時の対応 | 再起動の証拠不足、対象の置換、同期先の変化、配信の違いを切り分ける。古い receipt を再利用しない。 |
| 後片付け | 試験用の設定を戻し、後続の非回帰と一時権限撤去を確認する。 |

現行 [evidence_verifier.py](https://github.com/syuhei0519/application-manifest/blob/573b0a6712693e03807659da660cce2fcacff527/learning/account-release/evidence_verifier.py) は、再起動前後の記録、成功した同期 revision、両 controller の再起動証拠も要求します。画面の Healthy 表示だけで代用しません。

## 11. 全体非回帰と一時権限撤去

| 項目 | 内容 |
| --- | --- |
| 前提 | 復元対象の確認を終え、試験で変更した宣言・入力・権限・一時プロセスを列挙できる。 |
| 目的 | 目的の復旧に加えて、周辺サービスへの悪影響や余分な権限が残らないことを確認する。 |
| 操作 | **読み取り中心**：影響範囲の API / 画面 / 依存先 / 監視 / Argo の状態を既存の受入条件で確認。**状態変更**：承認された一時権限・設定・試験入力・一時プロセスを撤去する。 |
| 観測点 | 各受入条件、Git と runtime の一致、必要な観測の継続、期限・権限の残存、未確定要求。 |
| 期待結果 | 既存機能と監視が正常で、一時的に増やした権限が残らず、必要な通常権限は維持される。 |
| 不合格時の対応 | 失敗と未確認を分けて記録し、全体完了としない。既存共有 resource や本来必要な権限を一括削除しない。 |
| 後片付け | 実施した撤去と残した理由、最終確認時刻、未完了項目を記録する。Secret 値・token は公開しない。 |

## 今回共有された結果

2026-10-25 時点で検証担当から共有された範囲です。新しい実機採取ではなく、生ログを公開 CI にアップロードしたものでもありません。コード上の期待結果とは区別します。

| 対象 | 共有された事実 | 判定の範囲 |
| --- | --- | --- |
| metrics 欠測 | `InconclusiveAnalysisRun` により停止し、自動昇格しなかった。Collector は Git 復元済み。 | 欠測時の停止を確認済み。Collector 復元だけで全体復旧完了とはしない。 |
| timeout 後の同一要求 | 0.1秒の client timeout 後、監査で Abort 成功を確認。同じ ID / payload の再送で既存 `confirmed`、受理イベント1件。 | この Abort 要求について確認済み。他の全 API の試験済みという意味ではない。 |
| 前版の Git 変更 | 公開 [PR #79](https://github.com/syuhei0519/application-manifest/pull/79/files) が前版の source / image へ戻す差分でマージ済み。 | Git の事実。実際の最終適用・配信・再起動確認を代替しない。 |
| 最終復旧・全体非回帰・cleanup | 未完了。 | 完了扱いにしない。再起動・再同期を含む最終証拠と、後片付けの結果が必要。 |
| その他の正常系・異常系 | この教材では今回の個別実行結果を確定できる証拠を追記していない。 | 上記シナリオは期待条件。過去の PLAT-170 / 171–176 の成功を今回の実施済みに転記しない。 |

今後結果を追記する場合は、対象 SHA、観測時刻、操作の区分、実際の結果、証拠の保存先、未確認点を残します。失敗した試行と、修正後に成功した試行は分けてください。
