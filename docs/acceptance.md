# Core Platform v1 — PE019最終受入

4リポジトリ共通の受入正本。結果の索引は[PE019-acceptance.json](implementation/PE019-acceptance.json)。文書のみの統合commitと、実際に試験した以下の版を区別する。統合後はcharts/environments/bootstrapのGit object一致を確認する。

| リポジトリ | 実受入した保護main |
|---|---|
| frontend-app | ce6ec1d94cf1042a824b745f4a5f6367b1c0f845 |
| backend-app | 90de6cecbe4e140b4a5814a4c5018373b38b2359 |
| application-manifest | 66796bdca2824b1dee18cc8164ae553e8cb94248 |
| platform-gitops | 9c654586c7db8dff458d2934629abd6f96689649 |

## 原因・修正・短時間確認・本試験

- 原因：実Pod JSONの解析失敗と、Windows既定文字コード932に依存する出力読取。固定された公開模擬JSONはUTF-8で成功、932の誤復号ではJSON例外となる。古い実payload/offsetが残らない部分の一意原因は断定しない。
- 修正：明示UTF-8、完全出力と実終了コード、明示JSON解析、失敗位置/例外型/stack/終了コードの保存。元の不合格記録を保持する。
- 短時間確認：実CI稼働中120秒、同じ起動環境・8スクリプト、15秒ごと9連続保存と終了時保存、HTTP120・NR HTTP120/DB120・ログ一致に合格。安全な模擬例外は実exit1と位置/型/stackが保存され、終了後の所有行/プロセス/接続0。
- 本試験：短時間の全条件合格を保存した後、同一8スクリプト・元の閾値で900秒を実施し合格。native2919041561/build16978182121、900CRUD100%、61連続計測、CI最大1874735104bytes<2GiB、空き20%以上、OOM/eviction0、Pending60秒以内、Collector停止60.3415秒・復旧13.2375秒。

実試験区間は2026-10-06T18:07:25.7707369Z〜18:22:25.7707369Z。HTTPカウンターとヒストグラム増分900、現dashboard6クエリー、保存NR trace99ed68d750d9e9688f6faf9434aa8db7のHTTP-DB親子と900アプリログを確認。正常driver実exit0、worker自然終了、所有行/接続/プロセス0。

## AT全件の対応

下表の前段証跡は[実入力同値](implementation/evidence/PE019/PE019-final-audit/critical-input-reuse.json)と[前段hash監査](implementation/evidence/PE019/PE019-final-audit/precedent-checksums.json)を通して再利用する。過去NR traceを現在版の保存証明にしない。bcc→90の差は予算計測2スクリプト、manifest240→667の差はbackend選択release値。変更部分は今回の実120/900・現Pod結合・AT14で確認した。

| AT | 合格根拠・結果 |
|---|---|
| 01 | PE018Bのtrusted render/checksum/provenance fixed9/own9。現Podのschema2全12注釈とimageID一致。 |
| 02 | PE018BのMR HEAD/main競合拒否、fixed/own/source-target/CAS409/実tree。最終MR90/91も最新mainに照合。 |
| 03 | PE002BのDB遅延/180秒有限失敗/SQL中断/同revision resync/CRUD。Go移行/SQLは完全一致、migration Jobはrelease注釈以外一致、最終新kindもschema1。 |
| 04 | PE017Bの実DB異なる二run/第二failed不変保存/差替え拒否。現在のfull immutable record/SBOM readerで元buildと今回scanを結合。 |
| 05 | PE018Bの全中間main配備、旧未検査経路撤去。最終main2918186353全14/現MF667/Pod/900CRUD。 |
| 06 | PE012の2instance/rolling/同Podプロセス再起動/UUID/累積計測。SDK/APIの完全同値と現version/instanceのmetric/NR。 |
| 07 | PE014のstop/Recreate/NR断/429/queue/drop区分。最終停止中60/60CRUD、復旧中14/14CRUD、13.2375秒で受信・送信復旧。 |
| 08 | PE012–013の既知N300/p95/ゼロN-A/404/error、同一API/SDK/dashboard/Prom契約。最終RED6クエリーとHTTP900。 |
| 09 | PE010-AT09の同UID再起動1/異UID交換後0/KSM取得。基盤入力同値、最終Ready/restart/OOM連続計測。 |
| 10 | 今回900秒/実OCI+Trivy+SBOM時間重複/観測CRUD/資源と一時disk予算。暫定phase1試験は代用していない。 |
| 11 | PE015の固定Trivy dev-only lock fixture/include-dev-deps。PE018Bと今回の完全runtime SBOMを別scopeとして確認。 |
| 12 | PE017C/Bの固定project/path/URL/redirect/checksum差替え拒否。現在と歴史runの実fullSBOM GET/checksum/Reporter PUT403。 |
| 13 | 未保護jobのSHAタグ/cache/Package201を実測。凍結設計どおり運用制約を明記し、技術的書込不能は主張しない。現main検証失敗で下流停止。 |
| 14 | 現policy90/最新DBで歴史候補を新run再検査2918477811/実reader2918518060。実rollback/復帰の全注釈・schema/PVC・CRUDと元tree一致。 |

## DESIGN §10.1の8条件

| 条件 | 対応する証跡 |
|---|---|
| 1 新kind/Secret/F-B-DB CRUD | 新kind、8 Secret再注入、初期CRUD、最終900CRUD。 |
| 2 sourceからPodまで両サービス一貫経路 | 4保護main、source/build/scan/digest/record/SBOM checksum/MF/Argo/全注釈/実imageID、現reader。 |
| 3 不正image/旧MR/未検査/例外期限/scan異常停止 | 前段のactual negativeと現trusted境界・下流停止、入力同値。 |
| 4 ConfigMap/ReplicaSet/新Secret DB接続 | PE005 checksum/template/RS変更、今回復旧別DB markerをAPI経由読取・Secretと元DBへ復帰。 |
| 5 rollback/DB互換/PVC保護/restore | 今回AT14両leg、schema1/PVC21保持、backup SHA/復旧全行と履歴一致、復旧PVC337分離。 |
| 6 資源/API RED/HTTP-DB trace/log | 61サンプル、実RED、保存traceと直接HTTP-DB250組、現version/instance/log。 |
| 7 障害時API/停止・送信失敗・再起動区別/復旧 | PE014分離障害と今回Collector停止・復旧・CRUD/NR coverage。 |
| 8 配備digest SBOM/歴史候補追跡 | 完全run/SBOM checksum、原writer不変保持、最新policyの候補再検査とrollback。 |

## 図と現在構成

凍結8図の責任分担/経路/namespace/RBAC/Sync順/観測/供給網を照合し、[図のhash・ラベル・edge・差分](implementation/evidence/PE019/PE019-final-audit/diagram-contract-audit.json)に保存した。最終試験用kind名と隔離restore PVCは実施範囲の差分。Phase2は両サービス実装済み。Kong/Istio/Rollouts/Backstage/Crossplaneは将来計画であり実装済みとはしない。

実12 Apps Healthy/Synced、backend UIDf3fb9a1f-ddaa-41e0-9723-73eb46e3cf08、frontend UIDc6f46227-3f78-4e06-8f28-da6a29e29165、PVC21a31208-789a-4c23-9f28-389407f1b689。元Lab PVCf777/schema1を保持。

## 限界・運用と完了ゲート

NR絶対試験区間はHTTP834/DB834。900要求中66のHTTP spanは保存を確認できなかった。bounded500のHTTP250/DB250直接親子を現version/instance/logと照合した。全span無損失や連続資源ピークは主張しない。

AT13はprivate/信頼済み同一projectコード・外部MR禁止・保護資格・固定main/job/record/SBOM対応・registry cache無効という運用制約。New Relic8572010/US/契約100GB/追加0円、利用者容量確認済み、有料変更なし。実取り込みGBを取得したという主張はしない。

Secret/PAT/License/User API Key値は非保存。拒否されたprivate CI trace/private OCI host取得/cache削除/単独worker診断を別経路で再現していない。旧不合格索引・helperは保持する。

[再構築/復旧手順](operations/final-rebuild.md)、[bootstrap](bootstrap.md)、[最終証跡索引](implementation/PE019-acceptance.md)。この文書作成時点では技術受入は完了し、証跡main統合、単一Runner管理者引継ぎ、親19 Closed/全19読み戻しは最後の完了ゲートとして残る。
