# 最終版の再構築と受入

この手順は新しいkindからの再構築を対象とする。既存LabをHealthyと観測した結果だけで、新規構築やPE-019を合格にしない。具体的なsource SHA・digest・scan run・tool引数と時刻は試験ごとの固定profileと実測索引に記録する。

## 開始前

1. frontend-app/backend-app/application-manifest/platform-gitopsの保護mainと実行対象SHAを固定する。運用文書の変更もmainへ統合してから試験版を確定し、未統合sourceを配備済みとは扱わない。
2. `bootstrap/versions.lock.yaml`とアプリの`ci/tool-versions.env`を実バイナリ・Chart package checksum・CI job image/argsへ照合する。kind/Kubernetes/Argo/Runner/Prometheus/Collector/Grafanaを任意の最新版へ置換しない。
3. Windows/WSL-Docker/kind/runner-helper-sidecar/一時OCI・DB・SBOMの計測範囲を固定する。ホストとWSLの空きmemory、diskは20%以上、CI concurrencyは1、試験中のOOM/evictionは0、Pod Pendingは60秒以内とする。
4. New Relicの地域、既存License Secret、100GB契約の現在の取り込み余裕と追加費用0円の条件を確認する。未確認のままexporterを有効化しない。地域USでは`https://otlp.nr-data.net`を使用する。

## 新しいkindへの構築

1. 専用名/context/kubeconfigを指定し、固定node imageでkindを作る。既存contextを切り替えない。開始前のcluster一覧と新nodeの名前/UIDを記録する。試験用kubeconfigはGit外で所有者に限定し、値を証跡へ出さない。
2. `bootstrap/namespaces.yaml`をbootstrap所有者が適用する。ChartにNamespace作成やCreateNamespaceを追加しない。
3. 固定Argo Chartを`bootstrap/argocd-values.yaml`で導入する。Lua healthの待機と全体sync履歴が初回順序を判定できることを確認する。
4. [Secret契約](../secret-contract.md)に従いrepository/image/DB/Runner/観測の必要なSecretを再注入する。包括承認が対象操作を含む場合はその既存承認を記録する。既存clusterからのコピーは値をメモリと標準入力だけで扱い、キー/type/UID・一致と実認証の結果だけを残す。元のSecretや権限を再発行しない。
5. 検査済みの両imageと完全なrecord/run/SBOM/source/job権限の結合を確認してからrootを有効化する。`bootstrap/root-project.yaml`、`bootstrap/root-application.yaml`と`environments/local`を使用する。親のHealthyだけで末端準備を判断しない。
6. [末端待機](initial-leaf-readiness.md)でPostgreSQL→対象revisionのmigration hook→backend→frontendを待つ。schema履歴、実Pod UID/imageID/全検査注釈、frontend経由の所有CRUDと削除後404を確認する。新規構築結果を既存Labの結果で代用しない。
7. 観測のsource/version/instanceをPodと対応させ、CPU/memory/restarts、API RED、HTTP→DB trace、trace_id logの実結果を同じ版で確認する。旧sourceのNew Relic traceを再利用しない。

## 最終受入の範囲

数値profileは負荷前に固定する。900秒に1req/s、HTTP timeout5秒、SDK flush5秒、CRUD成功率99%以上、Collector停止60秒/120秒以内復旧を、OCI/Trivy/SBOM CIと同じ窓で評価する。CIを担当するRunnerは一つにし、既存/新kindのmanagerを合わせてconcurrencyを確認する。停止中のCRUD継続、queue/drop/欠損範囲、全job/helper/sidecarとhost/WSL/kind/diskの資源を実測する。観測できなかったspanを完全保存したとは主張しない。

設定変更/新DB接続、PVC backup/restore、最終policyで再検査した登録候補への理由付きrollbackと復帰を同じ実配備版へ結合する。元本DB/PVCは消さず、試験所有のデータだけを操作する。前段の契約/異常系は実入力一致とchecksumで再利用し、成功fixtureや旧manualを繰り返さない。AT01〜14を独立した14回の負荷試験に分割しない。

完了索引は各ATと設計8条件について実source/job/scan/SBOM/Pod/時刻/結果/checksumを対応させる。AT13の技術的書込禁止未達は運用制約と分けて保持する。実受入、main統合、親Issue Closedの読戻しと全19件の監査が一致してから完了とする。失敗した数値条件を事後に緩めない。
