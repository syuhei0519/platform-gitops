# ①監視 Research / Design — 2026-10-18

工程結論: 設計完了。backend変更なしで実装可能。製品ACの実測は未実施。
推奨は、platform-gitopsに学習専用の小さなHelm chartを追加し、既存Account SDK → 専用Collector → Prometheus → Grafanaの一本だけを配線すること。application-manifestにはAccount限定のopt-in送信設定を追加する。実装・静的検証はPlatformEngineering Cloud、実cluster受入だけ既存ローカルkind。

## 固定入力と担当AC

System正本はsyuhei0519/ai-development-system@ead1cc4023c083c7b1bf703d8b371a0957997ed5。agents/research-design.md、workflows/single-issue.md、contracts/handoff.schema.json、contracts/artifact-delivery.md、contracts/readiness.md、workflows/workflow-model-routing.mdを読んだ。Readinessと配送成功は別判定、最終AC判定はManagerだけが行う。モデル/effort/tierの実行証拠は取得できずunknown。進行中設定は変更しない。Manager側の元dispatch記録・予算を維持する。

2026-10-18にGitHub branch GETとCLI ls-remoteの両経路でmainを測定し、checkout HEADとも一致した:

| repository | measured main SHA |
|---|---|
| ai-development-system | ead1cc4023c083c7b1bf703d8b371a0957997ed5 |
| platform-gitops | 482ba450983ac2929f8176374d0d6ca50a69cac6 |
| application-manifest | a5b49532b2b39ad818419834a30c5113c2c90900 |
| backend-app | e030275abbf52ecd27b7152ce597b3d89be3f830 |

関連3repoはclean。適用対象の親と各checkoutでAGENTS.mdおよび.agents/skillsの有無を確認し、追加の適用指示は見つからなかった。System checkoutにも該当指示はない。依存cacheは本作業対象外。

以下のAC1〜AC5は原文の列挙用ローカルIDであり、既存Issue番号/ACを変更したものではない。公開出典は https://github.com/syuhei0519/platform-gitops/issues/83 。ACの文字列は原文と一致する。

1. AC1: Prometheus・Grafanaがローカルkindで動く。
2. AC2: Accountの要求数・エラー率・応答時間をダッシュボード1枚で確認できる。
3. AC3: 正常要求とエラー要求を流し表示の違いを確認できる。
4. AC4: 通信がない状態と収集できていない状態を区別できる。
5. AC5: 設定・ダッシュボード・確認手順がGitに残る。

対象外原文: 全サービス対応、ログ/トレース集約、通知、詳細SLO運用。
今回の工程はread-only調査と設計成果物の作成だけ。コード/Issue/PR/クラスタ変更、API課金、Secret/権限変更は実施していない。

## 根拠と既存Issue適合

詳細のcommit/path/blob/size/SHA-256はsources.jsonに固定した。Gitの全blob bytesから算出しており、差分の再生成や浮動main参照ではない。

- platform-gitopsのobservability/dashboards/application-overview.jsonとcharts/grafana/files/application-overview.jsonはbyte一致。要求rate/increase、5xx率、p50/p95、status_classクエリを再利用できる。全体ダッシュボードのコピーにはKSM/Pod Ready/外部trace案内など対象外が含まれるので、必要なパネルだけ新しい1枚へ移す。
- backend-app/internal/telemetry/telemetry.goはpe_http_server_requests counter、秒histogram、10秒の累積OTLP export、通信がなくても送るpe_go_goroutines gaugeを持つ。/metricsルートはない。HTTP middlewareはhealthを業務metricから除外し、method/route/status_classだけを付ける。
- application-manifest/learning/chart/templates/_helpers.tplにはOTEL設定がない。そのため現状の学習Accountの稼働だけでは収集開始を意味しない。
- SDKのendpoint検査はCollectorの固定FQDNまたはlocalhost/127.0.0.1、環境local、POD_NAMESPACE=account、buildCommit40桁を要求する。学習用namespaceへのDownward API namespaceをそのまま渡すと起動失敗する。見落としてbackend改修を要求しないこと。
- 既存environments/local/otel-collector.yamlは外部転送有効、Grafanaはoperator所有Secret、observability-foundationはnodes/proxy等のRBACを持つ。既存root/platformの有効化は今回の最小案に使わない。

platform-gitops#5はSLI query・目標・評価窓・error budgetが主題で、既存導入済みCapabilityを再利用する前提。今回ACにSLO目標/error budgetを足して#5の完了としない。area:observabilityのIssue検索では#4/#5/#17/#46を確認し、今回の導入ACと一致するものはなかった（全Issueの完全重複否定ではない）。#17は通知、#46は三種観測の障害解析で対象外。

推奨小Issue案（作成していない）: 「ローカルkindの学習AccountをPrometheus/Grafana 1枚で観測する」。主repo platform-gitops、関連repo application-manifest、epic sre-observability。#5の関連する導入前提/小子Issueとしてリンクし、#5自体のAC/paused履歴は維持。ACは上記原文5件、主Validation Layerはローカルkindの小さな観測シナリオ1本。Hard Dependencyは#5ではなく、既存Account稼働と承認済みimage/config配送。新Issue作成・親子/依存設定・Project更新はManagerの後続作業である。

## 確定する最小構成

独立したlearning/monitoring/chartをplatform-gitopsへ追加する。外部chartの新しい依存取得は不要。Prometheus/Grafana/Collectorの3 Deployment、3 ClusterIP Service、設定ConfigMap群、3個のtoken無効ServiceAccountを描画する。Prometheus/GrafanaはemptyDir、保持24h/上限256MB、1replica。再起動で履歴は失われるが、設定とdashboardはGitから復元される。このACは履歴永続化を要求していない。Argo/root既定値と既存observability chartは変更しない。

CollectorのService名/namespaceは既存SDK制約に合わせotel-collector.observability.svc.cluster.local:4318。observability内に既存同名資源があれば上書きせず停止する。その他はlearning-prometheus/learning-grafanaとして名前衝突を避ける。全資源にlearning.local/ownerとpart-of=learning-monitoringを付ける。SDK送信はAccountだけopt-in、他3サービスのrender bytesは維持。

imageは既存固定値を再利用する（実load可否・architecture・imageID照合は後続）:

- Prometheus: quay.io/prometheus/prometheus:v3.15.0@sha256:86b17a25c2db1d16a61b16b3c8f336679eb19e26333d03e808da387206e40faa
- Grafana: docker.io/grafana/grafana:13.2.3@sha256:d84563330dc9d2fd2bc096d0fb96021b5319c75bf6ed555566709998e823dec4
- Collector: ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector-contrib:0.161.0@sha256:b5cf983651c32c3ca13f936deb51742015a54d121f388cac248923ddeb8cc9fc

SDKはtrace exporterも同時に作るためCollectorの/v1/tracesはnopに渡す。trace保存/外部送信なし。metrics pipelineは既存memory_limiter/batchとPrometheus exporterを再利用し、send_timestamps=true、metric_expiration=90sを明示する。namespace補正は下記の専用resource processorで行い、他サービスを取り込まない。Prometheusはstatic scrapeのotel-application(8889)、self(9090)だけ。30秒scrape/10秒timeout、honor_labels=true、honor_timestamps=true、Kubernetes discoveryなし。config reload sidecarなし、ConfigMap checksumによるrollout。

Accountのnamespace互換は次を採用する。learning chartのtelemetry.enabled既定false、Account時のみ許可。SDK入力POD_NAMESPACE=accountを「既存SDKの互換用値」と明記し、DEPLOYMENT_ENVIRONMENT=local、OTEL_EXPORTER_OTLP_ENDPOINTは固定FQDN、POD_UIDはfieldRefの実uidを渡す。Collector専用pipelineではservice.name=account-backendと互換namespaceをfilter後、resource/upsertでk8s.namespace.nameをManagerが固定した実Account namespaceへ置換する。source version/instance/pod uidは保持する。互換値を実namespaceとして証跡・dashboardへ表示しない。実namespaceは非秘密valuesの必須inputで、任意の送信者認証を保証する機能ではない。SDK変更/DB移動/Account再buildは不要。実Account imageがこのSDKを含み40桁buildCommitを持つことはDeployment Ready時に確認する。

Grafanaはローカル学習用の匿名Viewerだけを採用。admin初期生成/basic login/signupを無効化し、dashboard/datasourceをread-only provisionする。Secretを生成/取得/使用しない。ClusterIPと127.0.0.1 port-forwardのみで使い、Ingress/NodePort/LoadBalancerなし。匿名Viewerはこの新規ローカルinstanceの設定で、既存ユーザー/cluster権限は変更しない。analytics/update/plugin auto install/external snapshotsも無効。既存認証付きwrapperをそのまま有効化する案はSecret必須なので不採用。

## 変更対象ファイル（後続Implementation）

| repo | path | 変更 |
|---|---|---|
| platform-gitops | learning/monitoring/chart/Chart.yaml | dependencyなしの学習専用chart |
| platform-gitops | learning/monitoring/chart/values.yaml, values.schema.json | 固定image/Account実namespace/owner、fail-closed入力 |
| platform-gitops | learning/monitoring/chart/templates/{serviceaccounts,collector,prometheus,grafana}.yaml | 上記3サービス、設定、volume、probes、resources、token無効 |
| platform-gitops | learning/monitoring/chart/files/account-overview.json | 既存queryを縮小した1dashboard、UID learning-account-overview |
| platform-gitops | learning/monitoring/README.md | 固定render/import/apply/負荷/欠測/復旧と証跡配送 |
| platform-gitops | learning/monitoring/validate.py | 小さなrender境界/設定/JSON検査 |
| platform-gitops | learning/monitoring/query-tests.yaml | promtoolによるnormal/error/idle/missing/reset fixture |
| platform-gitops | .github/workflows/ci.yml、必要ならci/github/test.sh | 既存checksを保持し、専用の静的検査を既存Helm実行経路へ接続 |
| application-manifest | learning/chart/values.yaml, values.schema.json | Account telemetry opt-in、他サービスtrueは拒否 |
| application-manifest | learning/chart/templates/_helpers.tpl | 上記4 envのみ、既存DB Secret参照は変更しない |
| application-manifest | learning/validate.py, learning/README.md | opt-in/他サービス不変/互換値の説明と受入手順 |

backend-appへの変更は0。Order/Payment連携工程との共有コード競合はない。application-manifestのlearning共通helperに別工程が触れる場合、Managerがtarget headを再固定してopt-in以外の差分が混ざらないことを独立Reviewで確認する。導入に不要なchartリファクタ/共通化は行わない。

## dashboardと欠測の仕様

パネルは①収集状態②要求数推定/5mとreq/s③HTTPエラー率(4xx+5xx、client/serverの意味を注記)④p95秒⑤status_class別req/s。5xxを起こすためにDBを停止しない。error確認は安全な400入力を使う。健康probeは分母から除外。

以下は実装するクエリ仕様。C/R/E/Fは説明用の略号であり、PromQLの未定義変数や新しいrecording ruleを要求するものではない。dashboard内には展開した式を格納する:

```
C = pe_http_server_requests_total{job="account-backend"}
R = sum(rate(C[5m]))
E = sum(rate(pe_http_server_requests_total{job="account-backend",status_class=~"4xx|5xx"}[5m]))
F = ((min(up{job="otel-application"}) == bool 1)
     * on() ((time() - max(timestamp(pe_go_goroutines{job="account-backend"}))) < bool 90))
    or vector(0)
```

F=1のときだけsum(increase(C[5m]))、R、(E or vector(0))/Rを表示し、率はR>0でもgateする。p95はhistogram_quantile(0.95,sum by(le)(rate(pe_http_server_request_duration_seconds_bucket{job="account-backend"}[5m])))をF=1かつR>0でgateする。rateをinstanceごとに計算後sumしcounter resetを保持する。増分は外挿した推定数であり、curl実行回数との厳密一致を要求しない。

- F=1、R>0: 収集正常/通信あり。正常だけならerror率0、400を混ぜると4xx系列/率が増える。
- F=1、R=0: 通信なし。要求rate/countは0、エラー率/latencyはN/A。0%成功と解釈しない。
- F=0: 収集不可/新鮮さ未確認。count/rate/error/latencyはN/A、statusは赤。upだけではCollectorのキャッシュとSDK停止を区別できないためSDK gauge timestampも使う。
- 初回要求前や新規起動直後、rate用2点未満: 収集準備中/要求系列未作成。F=1だけで通信ゼロと断定しない。
- Grafana→Prometheus障害はdatasource Error/No dataと表示し、成功や通信ゼロに変換しない。

90秒判定は10秒SDK+5秒batch+30秒scrapeに余裕を持たせたローカル受入上の最大鮮度で、詳細SLOではない。Exporter既定ではtimestamp未送信・expiration5mのため変更が必要。時計ずれが大きければfreshness検証は失敗扱いで止める。単一Account instanceを前提とし、複数instanceの完全欠測検出は対象外。

## 必須権限と禁止操作の抽出

新規CRD/Role/RoleBinding/ClusterRole/ClusterRoleBinding/Secretは0。PodのAPI権限も0、全SA token mount=false。KSM/cAdvisor/Operator/ServiceMonitor/Argo/外部APIは不要。

後続ローカル適用者には、正確なcontext/API endpointを明示した上で以下の既存権限が必要。権限不足なら追加grantせずManagerに返す:

- observability namespaceが未存在ならnamespace create/get/label（既存ならownership確認のみ）。新namespace作成自体はcluster-scoped操作。
- observability内の今回所有Deployment/Service/ConfigMap/ServiceAccountのget/create/patch、pods/events/replicasetsのget/list/watch、pods/portforwardのcreate。rollbackで今回作成資源を消す場合のみそのdelete。RBAC資源を生成する権限は不要。
- 実Account namespaceのdeployment/learning-accountのget/patch/watchとpodsのget/list、portforward。PodTemplate env変更による1回のAccount rollout、以前の承認済みtemplateへの復旧。DB/Secret/PVCは対象外。
- 読取のversion/nodes/context/namespace観測とimage import/kind load。kubeconfig表示は必要項目に絞りraw credentialを記録しない。docker/kind権限は既存認可を使う。

既存foundationを採用した場合だけ要求される不要権限（採用しないが正確に抽出）: ClusterRole lab-prometheus-kind-proxyのcore nodes get/list/watch、nodes/proxy getとClusterRoleBinding。account内Role lab-ksm-account-readのcore pods、apps deployments/replicasets/statefulsets get/list/watchとRoleBinding。既存Grafanaはgrafana-adminのadmin-user/admin-password、外部traceを有効にする既存Collectorはnewrelic-otlpのlicense-keyを参照する。Secret値は読んでいない。Prometheus community chartはOperator CRDを要求しないが、既存foundation/chartの一括適用は禁止範囲を広げる。

## 最小テストと担当AC

全ACの最早検証段階はpremerge。ローカルkindで候補固定manifestを手動適用できるためpostdeployへの延期理由はない。今回Researchのcoveredは設計責務だけで、全製品ACはunverified。

| AC | Cloud（独立Test） | ローカルkind（独立Test） |
|---|---|---|
| AC1 | Helm lint/template、Kubernetes1.36.4描画、image/volume/probe/inventory検査 | 固定image import後3 Deployment Ready、Service DNS/Prometheus ready/Grafana health |
| AC2 | JSON/schema、query fixtures、UID/provisioningとラベル確認 | 1枚のUIとPrometheus API値を同時刻に照合、p95有限・単位秒 |
| AC3 | normal/errorのfixture | GET /api/accountsの200、GET /api/accounts/0の400を各既知回数送りstatus別増分と率の差を見る |
| AC4 | idle/missing/cached gauge/reset/初回未作成fixture | 負荷停止後5m窓を抜けてF1/R0、専用Collector一時停止時F0/N/A、復旧後F1 |
| AC5 | 設定/dashboard/runbookのGit blobとhash検査 | 固定render/config/image対応、時刻付き非秘密結果を保存してManagerへ配送 |

Cloudで新chartをhelm lint/template --kube-version 1.36.4し、RBAC/CRD/Secret/PVC/Ingress/NodePort/外部exporterを描画していないことを検査。application-manifest既存learning/validate.pyを必要な変更後に実行、telemetry既定offの他サービスrenderが元pinと一致することを確認。promtool check configとtest rulesは同じ固定Prometheus image/toolで実行する。既存適用CIは保持するが、今回のために無関係なfull suiteを新設しない。

ローカルTestの一連の最小シナリオ:
1. Managerがcontext/cluster/API server/実namespace/Account UID/imageID/owner/同名資源なしをread-only照合し、mutationとcleanup対象を固定する。Cloudはkind接続しない。
2. Cloud固定render/image bytesを取得・全bytes hash照合し、必要なら承認済みimageだけimportする。3監視資源群→Account env設定の順に適用。Account既存image/DB/PVC/Secretはそのまま。
3. loopback port-forwardで正常GETを60回/約60秒、続いて正常GET+id0 GETを各60回/約60秒。SDK/scrapeを待ち、200/400とdashboard差を記録する。過負荷は不要。取得したAccountデータ本文/個人情報は証跡に保存しない。
4. 追加業務要求を止め5m+最大90秒まで待つ。gaugeは新鮮、rate0、ratio/p950扱いにならずN/Aであること。待機は有限pollで進捗を返す。
5. 今回所有Collectorのみreplicas1→0、2scrape以内にup失敗とF0を確認する。Cloud fixtureではCollectorが動いたままSDK heartbeatが古い場合もF0となることを検証。必要ならローカルでAccountのOTLP endpointだけを一時localhost未listenへ変更し、APIを止めず90秒のstale検知を1回確認する（追加rollout権限・復旧入力をManagerが固定した場合だけ）。
6. Collector1/Account元endpointを復旧しF1、正常GETの復帰を記録。Order/Payment/Notificationはread-only Ready確認のみ、全サービス機能試験は追加しない。

主受入は5までの一連のシナリオ。SDK送信停止のlive追加チェックはfixtureで十分なら不要。失敗時は自動再構築しない。証跡確保後Accountの今回追加envを元のPodTemplateへ復旧し、今回所有監視資源だけ削除または保持する。namespace自体と既存DB/PVC/Secret/他サービス/clusterは削除しない。停止前のreplicaとenvを保存して復旧責任をローカルTestへ明示する。

## Readinessと後続開始条件

技術判断は上記で確定し、API/DB設計の未決はない。Implementation Readyの設計面は満たす。正式なready判定はManagerが本成果物を取得・hash照合し、scopeを明示して別途実装を認可し、現在mainと他並行差分を再固定した時点。今回のResearch権限を実装権限へ変換しない。

Deployment Readyはpending: 正確なローカルtarget/既存資源/Account実image/buildCommit/実namespace/所有inventory/許可されたrolloutとcleanup/監視image loadをまだ観測していない。認可不足は既存権限の範囲で停止し、RBAC/Secretを追加しない。特にAccountのlegacy namespace補正をrunbookとrenderで明示することを独立Reviewの必須観点にする。

Merge Ready/Issue Completionはpending。2repoの通常PR相当、integration_scope=aggregate_requiredを推奨。新learning chartは既存Argo/rootから参照されず手動適用のみなので設計上deployment_effect=noneを推奨するが、Managerが現在CI/deploy接続を再確認して固定する。全ACをpremergeで測定し、current-head CI・独立Test・独立Review・GitHub必須条件・操作認可を保持する。両repoの必要統合と全AC実測後にManagerが最終判定する。#5のSLO完了を主張せず、自動closeキーワードも付けない。

残る入力は実装開始後のtarget/authorizationとruntime identity確認であり、設計候補を選び直すための未決ではない。既存imageがSDK未搭載/起動検査不一致、namespace衝突、image取得不可なら正確なtarget/理由とともにblockedで返す。backendを無断変更する代替は採らない。

## 一次資料

Exporter timestamp/expirationの根拠: https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/3f8455d8038a985398861171e5310bc9b4e988b2/exporter/prometheusexporter/README.md
Grafanaのadmin初期生成抑止設定の根拠: https://raw.githubusercontent.com/grafana/grafana/6193dc03311b631b9727b560d24369e683dc396e/conf/defaults.ini
PromQL関数の根拠: https://prometheus.io/docs/prometheus/latest/querying/functions/
これらの技術判断は推奨設計であり、実際のcluster挙動の証拠ではない。本ファイルは公開用edited_derivativeであり、公開前の原本とは別artifactである。公開記録とConsumer利用承認は別途扱う。
