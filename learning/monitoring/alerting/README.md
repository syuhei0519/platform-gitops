# Account 可用性 burn rate 通知（#17）

> Historical learning record. Its manual deployment route is retired for the existing kind.
> Use the [Git/PR → Argo deployment contract](https://github.com/syuhei0519/platform-gitops/blob/main/learning/existing-kind/DEPLOYMENT.md).
> The [original commands at fixed SHA](https://github.com/syuhei0519/platform-gitops/blob/cffb519cc26b08a41eb5c90d58698426ffc66789/learning/monitoring/alerting/README.md)
> remain available as historical evidence, not as a current execution procedure.


既存 `learning/monitoring/SLI.md` と `sli-queries.json` を変更せず再利用する。
共通 Prometheus chart の Alertmanager は無効で、学習 chart にも未導入だった。
そのため opt-in の専用 Alertmanager 1 Pod と、メモリ内に直近100通知だけ保持する
使い捨て Python Webhook 受信器 1 Pod を `learning-slo-alerts` に追加する。
既存 Prometheus/Collector/Account metrics を利用し、新規 RBAC・Secret・認証・外部通知はない。
両 Service は ClusterIP、Ingress/NodePort/外部送信先はなく、SA token を mount しない。
専用 namespace 自体は chart で作らず、runtime 担当が所有者・衝突を確認して用意する。
これは学習用のローカル通知経路で、永続保存・HA・人へのページングではない。

## 固定した判定

| 項目 | 値と理由 |
| --- | --- |
| 種類 | `AccountAvailabilityBudgetBurn` 1種類、severity `warning` |
| 元の契約 | rolling 5分、20要求以上、新鮮な up/heartbeat、histogram整合、2xx/3xx成功、可用性99% |
| burn | `(1 - availability) / 0.01`。表示のみ小数9桁丸め（境界の浮動小数誤差を除く） |
| 発火 | burn **2超**（失敗率2%超）が **2分連続**、30秒評価。単発の短い逸脱を抑制 |
| 通知 | group wait 5秒、group interval 10秒、repeat 1時間。同一 Service/namespace/alert をまとめる |
| 抑制 | state 0欠測、1準備/不完全、2無通信、3少量では burn が空なので発火しない。欠測を正常や0にしない |
| routing | alertname、warning、backend、実Account namespace の完全一致だけをローカル受信器へ。他は discard |
| 回復 | predicate が消えた次の評価で firing解除、`send_resolved: true`。**state=4かつburn<=2を確認して正常回復**とする |

resolved は「条件が消えた」通知であり、欠測への遷移でも届く。state 0/1/2/3 の resolved を
正常復旧と記録しない。欠測専用アラートや latency アラートは今回追加しない。
5分窓の全収集欠落や複数instanceの一部欠測を保証しない既存契約の制限も継承する。
これは短窓の学習用消費速度で、30日 SLO の累積予算ではない。

通知の `service=backend` はmetric/routingの識別子で、Kubernetes Service名ではない。
`service_url` は既存入力 `accountNamespace` と、明示values
`alerting.accountServiceName`（既定 `learning-account`）、`alerting.accountServicePort`（既定 `8080`）から組み立てる。
例：`http://learning-account.learning-services-20261018.svc.cluster.local:8080`。
実Serviceの名前/portを読み取りで照合し、異なる場合はこの2入力だけを上書きする。
通知には対象 `service=backend` / `account_namespace`、Service DNS の `service_url`、
`query` / `state_query`、Prometheus の `generatorURL`、この `runbook_url` を含む。
Service DNS はクラスタ内参照用。外部端末では下記 loopback port-forward で query を確認する。
runbook_url はこの手順を含む固定commitへのpermalinkで、branch削除後も参照できる。
受信器 `/events` は無認証のローカル試験専用。
通知以外の実データや秘密値を投入せず、port-forward は常に127.0.0.1に限定する。

## Cloud 差分検証

Helm 4.2.4、Python3（PyYAML 6.0.3 / jsonschema 4.26.0）、Docker を使う。
image の tag+digest は chart values/schema と check.sh に固定済み。
Prometheus v3.15.0、Alertmanager v0.32.0、受信器 Python 3.14.0-slim。

```sh
python3 learning/monitoring/validate.py --output .rendered/learning-monitoring
python3 learning/monitoring/alerting/validate.py .rendered/learning-alerting
sh learning/monitoring/alerting/check.sh .rendered/learning-alerting
```

既存 schema/strict lint/render 境界を確認し、opt-in は20資源（既存14+6）、追加 RBAC/Secret なしを検査する。
既存SLIの数値fixture19ケースから期待state/発火を生成し、pending→発火→正常回復、閾値ちょうど2、
短い逸脱を加えた22ケースを固定promtoolで実計算する。欠測・無通信・少量・4xx/5xx・resetを含む。
SLO全受入や通常アプリの障害注入は繰り返さない。

integration は外部到達不能の専用 Docker network 内だけに4コンテナを作り、合成exporter→
**実Prometheus→実Alertmanager→実受信器**の firing/resolved payload と参照先を検査する。
5分query/2分holdを維持し、scrape/evaluationだけ1秒へ短縮する。正常な合成要求を増やして回復させ、
state=4/burn<=2、以後通知が続かないことを確認する。ホストportは公開せず、finallyで自分のコンテナ/networkを削除する。
環境注入proxyは試験コンテナ内だけ空にする。試験出力は成功/失敗の要約だけで、payload/内部実行IDを公開しない。
CIの既存 monitoring step に差分検証を追加し、既存 security/required check を維持する。

## 実 kind 担当の手順（Cloudでは未実施）

必要権限は、既存承認済み kind/context の読み取り、専用 namespace の作成、
`learning-slo-alerts` 内 Deployment/Service/ConfigMap の作成・削除、
`observability` の `learning-prometheus-config` ConfigMap と `learning-prometheus` Deployment の更新、
Pod状態/ログの読み取りと Service port-forward。
新規 Role/RoleBinding/ClusterRole/Secret の権限は不要。
namespace作成権限がない場合は管理者に専用namespaceを用意してもらう。
既存 Collector 用 Role/Binding は変更しない。RBACやSecretが追加で必要なら適用を止めて報告する。

1. 固定commitをcheckoutし、既存READMEの所有者・context・Account source/image・衝突確認を実施する。
   `learning-slo-alerts` と2つの資源名に他所有者の資源があれば上書きしない。
   既存監視リリースがこのchart由来でない場合も適用を止める。
   非秘密valuesは既存owner/accountNamespaceを維持し、`alerting.enabled: true` と
   実Serviceに一致する `alerting.accountServiceName: learning-account` / `alerting.accountServicePort: 8080` を明記する。
   固定imageを対象kind nodeへ事前loadする。通常のAccount/Collectorを再起動しない。
2. `helm template learning-monitoring learning/monitoring/chart --namespace observability -f monitoring-values.yaml`
   でrenderし、専用namespaceの6資源、既存PrometheusのConfigMap/Deploymentだけを差分適用する。
   既存Pod-only Role/BindingやCollector configを含む全体applyを避ける。
   renderからの抽出例（無効化時も同じ抽出で既存Prometheus2資源だけが残る）：

   ```sh
   helm template learning-monitoring learning/monitoring/chart --namespace observability -f monitoring-values.yaml > monitoring-alerting-render.yaml
   python3 - <<'PY_DELTA'
   import yaml
   from pathlib import Path
   objects = list(yaml.safe_load_all(Path('monitoring-alerting-render.yaml').read_text()))
   delta = [o for o in objects if o['metadata']['namespace'] == 'learning-slo-alerts' or
            (o['kind'], o['metadata']['name']) in [('ConfigMap', 'learning-prometheus-config'), ('Deployment', 'learning-prometheus')]]
   Path('monitoring-alerting-delta.yaml').write_text(yaml.safe_dump_all(delta, sort_keys=False))
   PY_DELTA
   ```

   未適用のnamespaceは担当者の承認済みownerラベルで作成する。
   Prometheusはconfig checksum変更によるrolloutでルール/routeを読み込む。
3. 対象contextを明示して3 DeploymentのReadyを待つ。`observability/learning-prometheus` は既存、
   専用namespaceの `learning-alertmanager` / `learning-alert-receiver` は新規。
   Prometheus `/api/v1/rules` の health=ok、Alertmanager `/api/v2/status` の設定がローカルだけか確認する。

   ```sh
   kubectl --context "$KUBE_CONTEXT" -n observability port-forward --address 127.0.0.1 svc/learning-prometheus 19090:9090
   kubectl --context "$KUBE_CONTEXT" -n learning-slo-alerts port-forward --address 127.0.0.1 svc/learning-alert-receiver 18080:8080
   ```

4. 他担当とAccount実測時間を調整し、既存backendの承認済みload guardから低rateの少量要求を行う。
   少数sampleでは state=3・burn空・通知なしを確認し、少量だからと無理に負荷を上げない。
   背景通信も含むため実窓のrequestsを読む。正常runはstate=4になればburn<=2・継続通知なしを記録する。
5. 発火差分は、既存の意図した400混在profileだけを通常アプリを壊さず低rateで実行する。
   `sli-queries.json` のrequestsが20以上、state=4、burn>2を2分継続できた場合だけ firing を期待する。
   少量・評価不能なら保留。通知 `/events` に `status=firing` と対象Service/query/runbook参照があることを確認する。
   DB停止、Pod削除、通常route改変、外部Webhook送信はしない。
6. 400混在を止め、同じ許可済みguardで正常profileへ戻す。過去失敗が5分窓から抜けるまで最大5分+評価30秒を見込む。
   state=4/burn<=2と`status=resolved`、以後正常通信中に継続しないことを確認して回復とする。
   全通信を止めた後はstate=2へ遷移しうるが、これは正常回復の代用ではない。
   実測値・公開可能なsource/image・結論だけを記録し、受信payload全文/内部ログ/実行IDは公開しない。
7. 復旧は `alerting.enabled: false` でrenderした既存Prometheus ConfigMap/Deploymentへ戻してReady確認後、
   今回所有の6資源のみ削除する。namespace全削除は所有と空を確認した場合だけ。
   受信器のメモリ通知はPod削除で消える。既存Collector/Grafana/AccountとRBACを削除しない。

実Podでの受信・鮮度・負荷・回復・runtime権限は別担当の検証対象。
Cloud成功は実kindのAC受入や独立Reviewを意味しない。
