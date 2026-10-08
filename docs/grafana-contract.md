# PE-013 Grafana provisioningと表示項目

公式community chart13.2.7を固定package SHA256 5189a48131311513ef7bf5e22f1ed6424cd1c7cec61bc1995fab04d3cf46f36aでvendorし、wrapperから使う。Grafana13.2.3のimageは固定digest。1replica/Recreate、CPU request100m/limit500m、memory128Mi/512Mi、ephemeral16Mi/128Mi、protected PVC1Gi。root init/sidecar/remote dashboard fetch/追加plugin/API資格情報を使わず、foundation所有SA grafanaはtoken mountなし。adminはoperator所有grafana-admin SecretKeyRefだけでGit/renderに秘密値を含めない。

Prometheus datasource UID prometheusをGitからprovisionし、ClusterIP内部HTTPへ接続する。名前・UID・非editable設定と2dashboard UIDは再起動後も同じ。標準30秒scrape・export10秒・表示窓5分を注記する。正本observability/dashboards/*.jsonとchart同梱files/*.jsonはCIでbyte一致を要求する。手動UI編集を運用手順にしない。

| DESIGN §7.3表示項目 | 実装panel / 出典と範囲 |
|---|---|
| 名前空間/Pod CPUコア数 | 基盤1 / cAdvisor各container rate→namespace/pod合算 |
| ワーキングセットメモリ | 基盤2 / cAdvisor namespace/pod合算 |
| 再起動増分 | 基盤3 / KSM account内5分increase、他namespace未収集を0と扱わない |
| Pod Ready/Pending | 基盤4/5 / KSM account、condition=true / phase=Pending |
| Prometheus対象up | 基盤6 / 全5scrape job、欠落は正常と扱わない |
| Collector送信失敗 | 基盤7 / send_failedはretry可能、最終dropと混同しない |
| queue/refused/dropの補助観測 | 基盤8/9/10 / 自己counter取得不能はN/A、enqueue failureをsend_failedと分離 |
| API要求頻度/件数 | アプリ1/2 / instance別rate/increase後に合算、backend到達分・health除外 |
| 5xx比率 | アプリ3 / status_classの5xx、分母0は系列除外でN/A |
| p50/p95応答時間 | アプリ4/5 / 秒explicit histogramの各instance rate→le合算 |
| 状態コード分類別 | アプリ6 / status_class別rate |
| backend Pod Ready | アプリ7 / account/backend Pod |
| source SHA / service.version | アプリ8/9 / target_infoとjob/instanceで結合、SHAを全histogramへコピーしない |
| NR trace案内 | アプリ13 / US account8572010のtrace.id/version検索手順、キー非露出 |
| runtime/SDK障害補助 | アプリ10/11/12 / Go goroutines/allocated bytes/export errors（全span lossの確定ではない） |

最小6指標という数で受入を代替しない。PE-013Aはprovisioning/基盤表示/再起動再適用、Bは実APIとAT-06/08のinstance/count/p95/N/A・NR検索で受け入れる。現時点のJSON/render/schema/policy成功は実画面・実API受入を代替しない。Collector自己metricは初回エラーまで系列未生成の場合があるのでN/Aを表示し、仮想0で異常不存在を主張しない。

実画面を開いた際に初期上限256MiでOOMKilled/exit137/restart+1を実測したため、limitを512Miへ改訂する。request128Mi/CPU/replicaは保持。PE-014の15分同時負荷で使用量・全体予算・OOM不発を再確認し、今回の変更だけで予算受入にはしない。

版表示はPrometheusのinstant/table形式でjob・instance・service_versionを列表示し、rate legendにもinstance/SHAを出す。時系列のvalue=1だけで版が表示されたと扱わない。5xx比率の範囲は0〜1（percentunitで0〜100%）。NRQL trace検索は公式例と同じ trace.id（引用なし）を使う。実アカウントで旧新版4spanとHTTP→DB親子関係を照合済み。backtick版のNoDataを送信失敗と扱わない。
