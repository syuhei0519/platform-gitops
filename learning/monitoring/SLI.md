# Accountの学習SLI（#5）

単一Account instanceの業務HTTPを既存Prometheus/Grafanaで比較する。
学習用の短いrolling **5分窓**、**20要求以上**を評価可能とする。本番SLAや統計的保証ではない。
全サービスSLO、長時間/限界負荷、alert、SLO gate、自動Canary判定は対象外。

## 分母・分子・目標

`pe_http_server_requests_total{job="account-backend"}` の各instanceに対し
`increase(...[5m])` を求めてからsumする（再起動のcounter resetに対応）。
route/method/status_classは既存の有限labelを使う。health probeは既存SDKで非計測。
業務routeの全methodを対象とし、4xx・5xxや意図した400も分母から除かない。

| 指標 | 定義 | 学習用目標 |
| --- | --- | --- |
| 可用性 | 2xx+3xx要求数 / 全業務HTTP要求数 | 99% |
| error率 | 1−可用性（4xx+5xx、その他非成功class） | 1%以下 |
| latency | 0.25秒以下bucket増分 / histogram count増分 | 95% |
| 可用性budget残率 | 1−(1−可用性)/0.01 | 0以上 |
| latency budget残率 | 1−(1−latency)/0.05 | 0以上 |

250msは既存histogramの**実bucket境界**。latencyは失敗応答も含む全要求の速さであり、
速い500でもlatency成功・可用性失敗になる。可用性と独立に読む。
許容失敗数は窓の分母×0.01、許容slow数は分母×0.05、消費率は失敗数/許容数。
budget残率は負値を隠さない。300要求で10失敗なら可用性96.667%、残率−233.333%。
3xxはサーバSLIでは成功、k6シナリオでは契約外として失敗（redirect追跡なし）。
400混在profileは意図的に可用性/budgetを悪化させる。別profileと混ぜて正常扱いしない。

正本の即時PromQLは `sli-queries.json`。dashboard `learning-account-overview` 下段へ同じ式を展開し、
recording ruleや外部サービス追加なしで表示する。既存6 RED panel/停止・復旧判定は保持する。
UIのN/Aをゼロや成功へ変換しない。statはinstant query、percentunit（1=100%）。

## 通信なし・欠測・少数sample

既存REDと同じ `up{job="otel-application"}` とSDK gaugeのtimestampを使う。
up=1、gaugeの年齢0以上90秒未満を要求する。Collectorに残るcacheを稼働と誤認しない。

| SLI状態 | 意味 | SLIとbudget |
| --- | --- | --- |
| 0 | up不明/0、heartbeatなし/古い/未来時刻 | N/A、健全と判定しない |
| 1 | HTTP初回系列なし/2点未満、histogram count/bucketなし、count不一致 | 準備中/不完全、N/A |
| 2 | 新鮮で要求増分0 | 通信なし、比率/budgetはN/A |
| 3 | 新鮮で要求増分0超20未満、histogram整合 | 少数sample、比率/budgetはN/A |
| 4 | 新鮮・要求20以上・histogram整合 | 数値を評価、目標未達もそのまま表示 |

histogram countとHTTP countの増分を0.001未満の差で照合し、threshold bucketの存在を要求する。
rate/increaseは外挿推定のため、k6要求数との厳密一致や整数性を仮定しない。
短いrunでも低sampleなら延長・増負荷で無理に合格させず、判定保留を記録する。
Prometheus接続障害はGrafana Error/No data。単一instance前提であり、複数instanceの一部欠測の
完全検出や窓中の全収集欠落の補正はしない。新鮮さは現在時点の判定で、窓全体の完全性の証明ではない。

## revisionと実行比較の限界

SDKのresource `service.version`（source SHA）、`service.instance.id`、Pod UIDはCollectorで保持され、
`target_info`に載る。HTTP系列はjob/instanceとmethod/route/status_classを持ち、version/run labelを直接持たない。
今回のSLI式は現行単一Accountをaggregateし、revision別・run別のCanary判定は実装しない。
将来は `on(job,instance) group_left(service_version)` でtarget_infoを結合できるかliveで確認し、
同時revisionとinstanceの対応を検証する必要がある。run IDは現状metricにない。
計装全改修には広げず、k6 artifactの公開label・入力revision・UTC時刻とtarget_infoを照合する。

ローカルTest担当は実行直前に対象cluster/context/namespaceとAccount source/imageを再確認し、
確認済みloopback転送先だけへbackendのguard入口で実行する。各profileを別の同長窓で評価し、
既存背景通信を含む点を記録する。SLIの秒とk6のclient latency msは異なる測定である。
正常/400混在、停止後5分+鮮度待ちの通信なしを同じqueryで確認する。
欠測・500・slow・resetはCloud fixtureで確認でき、実DB停止などの障害注入は不要。
現在稼働のsource/image、保存データ、他サービス、監視停止/復旧の承認境界を保持する。

## 再現

既存hookをそのまま使う。新規root CI編集/追加hookは不要。

```sh
python3 learning/monitoring/validate.py --output .rendered/learning-monitoring
sh learning/monitoring/check-tools.sh .rendered/learning-monitoring
```

`validate.py` はSLI式とdashboardの完全一致を確認し、`sli-fixtures.yaml` の数値期待値から
一時promtool fixtureを生成する。固定Prometheusのpromtoolで正常、4xx、5xx、全失敗、slow、
idle、初回なし、少数sample、up/heartbeat欠測・stale、histogram欠測、counter resetを実計算する。
既存10 RED fixture・Helm strict lint/render・12資源境界・Collector config検証も同時に維持する。
Cloud結果はlive計測の代わりにしない。実適用は後続の承認済みローカル担当が行う。
