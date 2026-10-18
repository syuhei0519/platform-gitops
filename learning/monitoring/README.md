# ローカルkindのAccount監視

Account SDK → 専用Collector → Prometheus → Grafanaの1経路を、既存Argo/rootや
observability foundationから独立させる。backendコードは変更しない。
要求数推定/5m、req/s、HTTPエラー率（4xx+5xx）、p95秒、status_class別req/sと収集状態を
`learning-account-overview` の1枚で見る。health probeは業務要求に含まれない。

このディレクトリの検証はCloudの静的検証とPromQL fixturesであり、kindでの5AC実測は未検証。
独立Test/Reviewと両repoの統合・最終判定は別工程で行う。関連:
[platform-gitops#83](https://github.com/syuhei0519/platform-gitops/issues/83)。
#5の詳細SLO、全サービス、logs/traces集約、通知は扱わない。

## 境界と前提

- 手動適用のみ。namespaceは固定の`observability`。同名のCollector等があれば停止し、既存資源を上書きしない。
- `owner` と実Account namespace（専用`learning-*`）が必須。imageはvaluesの3つのdigestに固定。
  ローカルtargetのarchitecture、load可能性と実imageIDは後続Testで照合する。
- 3 Deployment/Service/ConfigMap/ServiceAccountの計12資源。全てownerラベル付き、token自動mountなし。
  RBAC/CRD/Secret/PVC/Ingress/外部exporter/Kubernetes discoveryを追加しない。
- Prometheusは24h/256MBまでのemptyDir、GrafanaもemptyDir。再起動で履歴/DBは消える。
  ConfigMapから設定とdashboardは復元される。保持時間の保証や永続化はACに含めない。
- 新規Grafanaは匿名Viewer・basic/login/signup/admin初期生成無効のローカル学習用設定。
  read-only provision、更新確認・分析送信・plugin自動導入・外部snapshot無効。
  この設定コードへの承認はlive適用の承認ではない。匿名Viewer構成のlive適用は別途Manager承認が必要。
  ClusterIPと`127.0.0.1` port-forwardだけを使用する。

## Accountのnamespace互換と起動条件

[application-manifestのAccount opt-in](https://github.com/syuhei0519/application-manifest/tree/main/learning)
で`telemetry.enabled: true`とする。既定はfalse、他サービスのtrueはschema/templateで拒否する。
4入力はAccount API専用helperに限定し、完了済みmigration JobのPodTemplateは変更しない。
既存DB/Secret/PVC/image参照は変更しない。

既存SDKは送信時に40桁buildCommit、environment=local、`POD_NAMESPACE=account`、
固定HTTP endpoint `http://otel-collector.observability.svc.cluster.local:4318` を要求する。
`POD_NAMESPACE=account`は**SDKのlegacy互換入力であり、実namespaceではない**。
Pod UIDはDownward APIの実uid。専用Collectorはservice.name=account-backend、local、互換namespaceを
filterしてから`k8s.namespace.name`を`accountNamespace`へupsertし、version/instance/Pod UIDを保持する。
`target_info`の`k8s_namespace_name`とdashboard説明は実namespaceを使う。

この属性検査は送信者認証・namespace隔離ではない。任意の送信者が属性を偽装できるため、
学習用の承認済みtargetに限定する。固定backend参照のCollector契約もこの検査を送信者認証とはしていない。
実SDK/環境のnamespace制約が保護条件・セキュリティ境界として運用されていると判明した場合、
互換値で回避せず停止してManagerへ報告する。SDK非搭載/起動検査不一致も停止対象。backendを変更しない。

## 表示の意味とCloud検証

SDKは累積counter/histogramと無通信でも10秒周期のgoroutines gaugeを送る。
Collectorは`send_timestamps=true`、`metric_expiration=90s`、Prometheusは30秒scrape、
`honor_labels/honor_timestamps=true`。SDK時刻をscrape時刻で上書きしない。

| 状態 | 条件 | 表示 |
| --- | --- | --- |
| 収集不可/新鮮さ未確認（0） | up不明/0、SDK gaugeなし/90秒以上古い/未来時刻 | REDはN/A |
| 要求系列の準備中（1） | SDK新鮮、要求未発生またはrate用2点未満 | REDはN/A |
| 通信なし（2） | SDK新鮮、5m要求rate=0 | count/rate=0、error/p95=N/A |
| 収集正常/通信あり（3） | SDK新鮮、要求rate>0 | normalのみはerror=0、400混在で4xx率上昇 |

鮮度は`up`とSDK gaugeの`timestamp()`を併用する。CollectorのキャッシュをSDK稼働と誤認しない。
単一Account instanceが前提。複数instanceの一部欠測の完全検出は対象外。
大きな時計ずれは失敗として扱う。Prometheusへの接続障害はGrafanaのError/No dataであり通信ゼロに変換しない。
rateはinstanceごとに計算してからsumする。増分は外挿推定値でcurl回数との厳密一致ではない。
p95はbucket推定で単位は秒。全クエリはdashboard内へ展開し、recording ruleに依存しない。

Helm4.2.4、Python3/PyYAML6.0.3/jsonschema4.26.0、Dockerを使う。clusterへの呼出しはない:

```sh
python3 learning/monitoring/validate.py --output .rendered/learning-monitoring
sh learning/monitoring/check-tools.sh .rendered/learning-monitoring
```

schema/strict lint/1.36.4 render、12資源境界、設定/provisioning/JSONと正確なdashboard式のfixture対応を検査。
`check-tools.sh`はvaluesと同じ固定Prometheusのpromtoolでconfigと10シナリオを検証し、
固定Collectorでconfig validateのみを行う。normal/4xx/idle/missing/stale/heartbeatなし/upなし/
初回系列なし/2点未満/counter resetを含む。NoDataを一律0にする変換は入れない。
既存CIのpolicy/checksum/security/schemaゲートは保持する。

## ローカルkind受入（後続の承認済みTest担当者向け）

以下は操作手順の記述であり、実施結果ではない。実行前にManagerが正確なcontext/API endpoint、
cluster、実Account namespace、owner、image/source/config bytes、現在replicas/envと復旧責任を固定する。
namespace衝突、image不一致、既存権限不足は停止。権限追加/Secrets読取/再build/自動再構築はしない。
新規namespace作成、Account rollout、Collector一時停止/復旧、匿名Grafana適用とcleanupの対象・承認を別々に確認する。

監視inventoryはobservability内の各Deployment/Service/ServiceAccount
`otel-collector`、`learning-prometheus`、`learning-grafana`と、各`<name>-config` ConfigMap。
namespaceそのものはchartに含まれず、既存か承認された事前作成が必要。
`get/create/patch`、pods/events/replicasetsの`get/list/watch`、pods/portforwardの`create`、
所有資源cleanupの`delete`とAccount deploymentの既存`get/patch/watch`権限を確認する。
必要権限がなければRBACを追加せず返す。初回はinventoryの同名資源が全て不在であることを確認する。
監視opt-in側のinventoryは実Account namespaceの`apps/v1 Deployment learning-account`だけ。
適用/復旧ともJob、DB/StatefulSet、PVC、Secret、ConfigMap、Serviceを含めず、migrationを削除・再実行しない。

1. 非秘密valuesファイルに承認済み`owner`/`accountNamespace`を記録し、固定chartからrenderする。
   render/values/dashboardのSHA256とimage digestを固定して配送し、ローカルで全bytes照合する。
   Account側は既存の固定済み完全values（opt-in off、source/image/DB等を保持）から
   application-manifestで次を実行し、Deployment-onlyの3ファイルを固定bytesで配送する。
   全chart renderやServiceを含むAPI template全体を適用ファイルにしない。

   ```sh
   helm template learning-monitoring learning/monitoring/chart \
     --namespace observability --kube-version 1.36.4 -f monitoring-values.yaml > monitoring-render.yaml
   sha256sum monitoring-render.yaml monitoring-values.yaml
   ```

   ```sh
   # application-manifest checkoutで実行。新規directoryを指定する。
   python3 learning/validate.py --account-telemetry-only \
     --account-values existing-account-values.yaml --namespace "$ACCOUNT_NAMESPACE" \
     --output account-telemetry-delivery
   ```

   `account-api-before.yaml`（復旧用）、`account-api-telemetry.yaml`（適用用）、`inventory.json`を
   取得し、inventoryのnamespace/name/owner、両YAMLのsize/SHA256、各1 Deploymentのみを照合する。
   実AccountとbeforeのPodTemplate不一致は停止。合成fixtureを実対象の適用に流用しない。

2. 対象確認・live承認後のみ、固定renderを適用し、3 Deployment Ready、3 Service、
   実imageID、ConfigMap checksum、所有ラベルを確認する。
   その後`account-api-telemetry.yaml`だけを適用し、Accountだけのrolloutを確認する。
   完了済みJob/DB/PVC/Secret/他サービスは適用対象に含めない。

   ```sh
   kubectl --context "$KUBE_CONTEXT" apply -f monitoring-render.yaml
   kubectl --context "$KUBE_CONTEXT" -n observability rollout status deploy/otel-collector --timeout=300s
   kubectl --context "$KUBE_CONTEXT" -n observability rollout status deploy/learning-prometheus --timeout=300s
   kubectl --context "$KUBE_CONTEXT" -n observability rollout status deploy/learning-grafana --timeout=300s
   kubectl --context "$KUBE_CONTEXT" apply -f account-telemetry-delivery/account-api-telemetry.yaml
   kubectl --context "$KUBE_CONTEXT" -n "$ACCOUNT_NAMESPACE" rollout status deploy/learning-account --timeout=300s
   ```

3. 別terminalで各loopback port-forwardを起動し、終了時の停止責任を持つ。

   ```sh
   kubectl --context "$KUBE_CONTEXT" -n observability port-forward --address 127.0.0.1 svc/learning-prometheus 19090:9090
   kubectl --context "$KUBE_CONTEXT" -n observability port-forward --address 127.0.0.1 svc/learning-grafana 13000:3000
   kubectl --context "$KUBE_CONTEXT" -n "$ACCOUNT_NAMESPACE" port-forward --address 127.0.0.1 svc/learning-account 18080:8080
   ```

   `http://127.0.0.1:19090/-/ready`、`http://127.0.0.1:13000/api/health`を確認。
   `http://127.0.0.1:13000/d/learning-account-overview`を開く。
   Prometheus APIの`up{job="otel-application"}`と`target_info{job="account-backend"}`で
   service/version/instance/Pod UID/**実namespace**を固定Accountと照合し、Service DNSの配線成功を確認する。

4. 正常GET60回/約60秒、その後正常GETと安全な400を各60回/約60秒送る。
   本文は保存せずHTTP statusだけを記録する。DB停止等で5xxを作らない。

   ```sh
   for i in $(seq 1 60); do
     curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/api/accounts
     sleep 1
   done
   for i in $(seq 1 60); do
     curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/api/accounts
     curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/api/accounts/0
     sleep 1
   done
   ```

   SDK10秒+batch5秒+scrape30秒の余裕を取り、正常200・エラー400を確認。
   同一UTC評価時刻でdashboardの6式をPrometheus `/api/v1/query`にも送り、UI/API値を照合する。
   要求数推定増加、4xx率増加、p95有限/秒、収集状態3を記録する。

5. 負荷を止め5m窓+最大90秒まで有限pollで待ち、進捗を返す。
   SDK gaugeは新鮮で状態2、count/rate=0、error/p95=N/Aとなることを記録。
   初回系列なしをidleに読み替えない。SDK停止時のstaleはCloud fixtureで検証し、追加live rolloutは必須にしない。

6. 事前に固定した所有Collectorだけをreplicas1→0とし、2scrape以内にup失敗・状態0・RED N/Aを確認する。
   collector1へ戻しReady後、gauge更新・状態復帰と正常GETを確認する。
   AccountやDBを停止する操作に置換しない。Order/Payment/Notificationはread-only Ready確認だけ。

7. 時刻付きの非秘密結果、render/config/image identity、UI/API照合、停止/復旧/cleanupを保存する。
   Accountは保存済みの`account-api-before.yaml`だけをapplyして承認済みPodTemplateへ復旧し、
   Account rolloutを確認する。全chart renderで復旧せず、Jobの削除・再実行はしない。
   所有監視inventoryのみ保持または削除する。
   namespace自体、既存DB/PVC/Secret/他サービス/clusterを削除しない。
   kubeconfig/raw認証情報/Account本文/内部実行IDを公開物に含めない。
   Managerへ固定bytes/Handoffを配送し、5ACの実測と両repoの統合完了はManagerが判定する。
