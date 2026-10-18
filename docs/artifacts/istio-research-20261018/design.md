# Istio準備：最小設計（Research/Design正本）
公開用edited_derivative。追跡先: https://github.com/syuhei0519/platform-gitops/issues/85 。原本との比較はProducer証言であり、独立した原本比較済みとの主張ではない。

2026-10-18。原本Researchの工程範囲は比較・推奨・根拠・設定surface・必要テストの確定のみ。原本作成ではGit書込み、クラスタ操作、権限変更、追加の有料API利用を実施していない。公開用派生版の配送許可は成果物公開だけに限定され、製品実装やクラスタ操作を許可しない。下記名称は固定の架空テンプレート入力であり、実targetを表さない。

## 1. 固定する判断
- Istio **1.31.2**、sidecar、単一cluster。与件はkind 0.33.0 / Kubernetes 1.36.4。実clusterの接続・実versionは今回未確認。
- 公式release tag 1.31.2のcommitは `e2f3a7949a358af7dd901a6f9ccd6ecdd7270271`。istiod・proxy・istioctlを同じpatchに揃える。
- 導入方式は公式Helm chartをHelm 4.2.4でoffline renderし、固定YAMLを将来手動apply。Helm install/upgrade、istioctl install、Operator、Argo自動同期は併用しない。base→istiod→学習fixtureの順。導入自体は別承認の将来工程。
- 制御namespace `istio-learning-system`、参加namespace `learning-istio-demo`、revision `learning-1-31-2`。既存4 API/DBのnamespaceは参加させない。
- 通信は同一参加namespaceの専用client→ClusterIP Service `mesh-demo:8080`→`mesh-demo-v1` / `mesh-demo-v2`。HTTP GET `/version`、応答本文は厳密に `v1\n` / `v2\n`。通常90:10、開始・復帰100:0。Ingress/Kong/外部URLは不要。
- 主repoはplatform-gitops。実装候補は独立した `learning/istio/` のみ。backend、application-manifest、既存root/bootstrap/versions.lock、learning/monitoring、共有values/schema/validate.py/READMEは編集しない。

## 2. 比較と根拠
| 選択 | 利点 | 今回の判断 |
| --- | --- | --- |
| sidecar | client EnvoyでHTTP要求単位のVS/DR重み付けを学べる。対象3 Podに閉じたdata plane | 採用。再作成とPodごとのEnvoy費用は許容し、CNI/node agentを追加しない |
| ambient | sidecar不要、L4 proxyをnodeで共有。L7 routingはwaypointが必要 | 非採用。CNI・ztunnel・waypointを含めると1経路の準備より対象が増える。ambientが未成熟という判断ではない |
| 公式Helm render | base/istiodを分離してRBAC/CRD/webhookまで差分・schema検査できる | 採用。raw manifestを保存し、PCで再描画しない |
| istioctl install | profileで簡単に開始できる | 不採用。今回のGit準備では固定chartとrenderを明示したい。istioctlは静的analyzeに使用 |
| 1.30.6 | K8s 1.36が対応範囲 | 支持される代替。1.31.2の方が現時点の新しい系列で、終了予定も後 |
| 1.31.2 | 1.32～1.37対応、公式にsecurity patchとして公開 | 採用。1.29は1.36非対応かつ2026-10-15 EOL。master/1.32開発版は使わない |

公式対応表はKubernetes minor対応を示すものであり、kind 0.33.0＋Kubernetes 1.36.4のこのclusterでの動作保証・実測ではない。Kubernetes 1.36はsupportedである。公式データのtestedK8sVersionsは「Tested, but not supported」の別欄であり、1.36のsupported表記とは異なる。この欄への非掲載だけから、当該構成での実測済みまたは未テストを断定しない。出典と固定commit/blob/元文書bytesのhashはsources.jsonに記録した。chart実体・CLI・imageの最終digestは未取得であり、実装で取得してversions.lock.jsonへ固定する。架空digestを実行用に流用しない。

| 対象候補 | 起動・副作用/依存 | 判定 |
| --- | --- | --- |
| Account複製＋GET | DB pool・既存telemetry設定を持つ。監視作業の所有境界と重なる。v1/v2識別も追加で必要 | 非採用 |
| Order→Payment | Order/PaymentのDBと書込み/再送意味論を含み、進行中の連携実装に依存 | 非採用 |
| Payment / Notification複製 | 通常起動時にDB対象検査が必要。POSTは記録を作り、識別fixtureだけの目的に過大 | 非採用 |
| 既存APIの/livezだけ | 安全な疎通には使えるがv1/v2到達先を区別できず、API複製時のDB設定も残る | 既存非参加smokeだけに使用 |
| 専用DB非接続fixture | 2つのserver設定とclientのみ。DB・migration・Secret・PVC・外部送信なし | 採用 |

fixtureは学習専用の2設定revisionであり、既存アプリの2つの製品releaseを試したという意味ではない。既存serviceを二重起動せず、未完成OrderPayment連携を前提にしない。

## 3. 実装へ渡す具体surface
全て将来の実装仕様であり、生成・Git追加・render済みとの主張ではない。
- `learning/istio/versions.lock.json`: Istio 1.31.2/source commit、Helm 4.2.4、K8s 1.36.4、istioctl version、公式chart全体の取得hash、pilot/proxyv2/fixtureのimage digest・config ID・archive hash/size/platformを別々に固定。tagだけの同一性判定を禁止。
- `install/base.values.yaml`、`install/istiod.values.yaml`、`bootstrap/namespaces.yaml`。
- `chart/`: 専用fixtureのService 1、Deployment 3、専用ServiceAccount、VS/DR。routing modeは`stable`=100:0と`split`=90:10のみ。defaultはstable。未知modeはfail。
- `fixture/`: DB非接続の小さなHTTP stubとclient。Go標準library程度で足りる。serverは /version と /livez のGETのみ、revision以外の環境情報を応答しない。v1/v2は同一固定imageを使いargsで識別。
- `validate.py`、`fixtures/`、`acceptance.md`: 当該surfaceだけを検査。汎用未知kind検証器や既存4 API全suiteの再構築を追加しない。
- `rendered/` とinventoryは実装工程の固定成果物。Gitに実target/Secret/実行ログを入れない。

既存platform本線のNamespace定義はbootstrap所有のまま維持する。ここで提案するnested bootstrapは、既存learning手動配備経路にならう独立した新規学習用namespace計画で、root chartから参照しない。fixture chartはNamespaceを生成しない。独立Reviewでこの例外境界を明示し、本線へ取り込む設計には黙って切り替えない。

base valuesの固定項目:
```yaml
defaultRevision: learning-1-31-2
global:
  istioNamespace: istio-learning-system
  enableReaderRBAC: false
```

istiod valuesの固定項目（公式chart 1.31.2で確認したkeys）:
```yaml
revision: learning-1-31-2
replicaCount: 1
autoscaleEnabled: false
resources:
  requests: {cpu: 500m, memory: 512Mi}
  limits: {cpu: 1000m, memory: 1024Mi}
cni:
  enabled: false
env:
  PILOT_ENABLE_GATEWAY_API_DEPLOYMENT_CONTROLLER: "false"
sidecarInjectorWebhook:
  enableNamespacesByDefault: false
meshConfig:
  rootNamespace: istio-learning-system
  discoverySelectors:
    - matchLabels:
        kubernetes.io/metadata.name: learning-istio-demo
    - matchLabels:
        kubernetes.io/metadata.name: istio-learning-system
global:
  hub: docker.io/istio
  tag: 1.31.2
  istioNamespace: istio-learning-system
  proxy:
    resources:
      requests: {cpu: 100m, memory: 128Mi}
      limits: {cpu: 500m, memory: 256Mi}
```

専用参加namespaceだけに `istio.io/rev: learning-1-31-2` を設定し、legacy `istio-injection` とambient labelは併記しない。control namespaceはinjectしない。baseline/plain用namespace計画はrev labelなし、mesh用は同じnamespace計画へrev labelを追加する。

CPU/memoryは学習用の提案予算で、性能検証済み値ではない。各fixture appはrequests25m/32Mi、limits200m/64Mi、各Deployment 1 replica、serverの更新strategyはRecreateとする。将来の実行前にinit container・Envoyを含む描画結果とnode余力を確認し、不足時に無断拡張しない。

Serviceは `selector: {app: mesh-demo}`、port `{name: http, port: 8080, targetPort: http, appProtocol: http}`、ClusterIP。server Pod labelsは `app: mesh-demo, version: v1|v2`。Deployment selectorはappとversionの両方で互いに重複しない。clientは別app labelでService対象にしない。

```yaml
apiVersion: networking.istio.io/v1
kind: DestinationRule
metadata: {name: mesh-demo, namespace: learning-istio-demo}
spec:
  host: mesh-demo.learning-istio-demo.svc.cluster.local
  exportTo: ["."]
  subsets:
    - name: v1
      labels: {version: v1}
    - name: v2
      labels: {version: v2}
---
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata: {name: mesh-demo, namespace: learning-istio-demo}
spec:
  hosts: [mesh-demo.learning-istio-demo.svc.cluster.local]
  exportTo: ["."]
  gateways: [mesh]
  http:
    - name: demo-only
      match:
        - method: {exact: GET}
          uri: {exact: /version}
      route:
        - destination:
            host: mesh-demo.learning-istio-demo.svc.cluster.local
            subset: v1
            port: {number: 8080}
          weight: 90
        - destination:
            host: mesh-demo.learning-istio-demo.svc.cluster.local
            subset: v2
            port: {number: 8080}
          weight: 10
      retries: {attempts: 0}
```

stable renderは同じhost/port/subsetでweightsだけ100/0にする。Service selectorは両serverを含むため、meshの外やVSなしでは90:10を期待しない。/livezや未知pathをweight測定に混ぜない。mTLS強制・AuthorizationPolicy・PeerAuthentication・障害注入・Rollouts・gateway・CNI・ztunnel・waypointは追加しない。sidecar標準のauto mTLS/CAは残り、STRICT化を意味しない。

## 4. 必要な静的テストと独立Review
主validation layerはCloudの限定manifest contract検査。実装工程の必須範囲:
1. pinned公式base/istiodと専用chartを `--kube-version 1.36.4` でrender。stable/split両モード、Namespace計画、全namespace-scoped資源の名前と所属を照合。
2. VS/DRはnetworking.istio.io/v1。Service/FQDN/8080/targetPort/Pod port、selector/subset labels、v1/v2の存在、weights整数・合計100・modeとの一致、client除外を検証。
3. 負fixture: undefined subset、不正host、port不整合、namespace逸脱、selector逸脱、weights不正/合計不正、未知mode、相反するinjection labelsを拒否。placeholder imageはstatic専用とし実行用lock欠落を拒否する。
4. 正fixture: stable100:0・split90:10・plain復帰が表現でき、専用3 Deployment/Serviceのみ。DB/migration/PVC/Secret値や既存サービスへのpatch、Gateway/Policy/Rolloutを生成しない。
5. istioctl1.31.2で `analyze --use-kube=false`。自己完結したNamespace・Service・Deployment・VS/DRを一緒に渡す。analyzerだけで全selectorやweight契約が保証されるとは扱わない。Istio CRD schemaは同じ公式release sourceから検査し、未知CRDを無視してPASSにしない。
6. init/sidecarを含む注入後PodSpecの確認（offline injectionで十分）、image/source pin、resources、webhook selectors、CRD/RBAC差分とinventoryを確認。新規namespaceのPod Security条件とinitの要求権限の衝突はDeployment Readyの阻害として記録する。
7. 同じ固定候補を独立Testと独立Reviewへ配送。Reviewは対象限定、権限の広さ、復帰順序、非秘密化、既存Issueを完了扱いしていないことを確認。実装者の自己検査・schema PASSを独立Reviewの代用にしない。

renderと静的テストは今工程では未実行。独立Reviewも未実施。既存repoの必須CI/security gatesは保持するが、この設計から全Feature再試験を追加しない。今回のResearchはTest/Review工程を代行しない。

## 5. 将来の限定確認手順（実施していない）
全て別の明示されたlocal cluster実施範囲・既存権限・cleanup承認が揃った場合のみ。context/cluster/API endpointはprivate入力に必須、既定targetなし。与件のversion・namespace不存在/所有UID・image/load identity・他mesh/既存CRDの有無をread-onlyで照合。既存mesh/同名resourceがあれば借用・上書きせず停止する。

A. before: 専用namespaceのplain client/v1とServiceだけでDNS経由GET /versionが200/v1。既存非参加4 APIは承認済みprobe経路で /livez と /readyz のGETを一度確認し、beforeのcode/image/config pinをprivate記録。DB操作はしない。
B. foundation: 所有計画のbase→CRD Established確認→istiodを適用。istiod ready、revision webhook/Service ready、非参加Podが新規injectされていないことを確認。
C. mesh: namespaceにrevを付けclient/v1だけ再作成。両Podのproxy ready・実imageID・XDS syncを確認。**v2を追加する前に**DRとstable VSを適用して収束を確認。v2を追加してreadyを待つ。Service DNSを使うclientからGETする。backend Podへのport-forwardはsource sidecarを迂回するため重み付け証明に使わない。
D. measure: warmupを計数から除外し、stable100:0を200要求（v1=200、v2=0、失敗0）→split90:10を2000要求→stable100:0を200要求。負荷はconcurrency1・client timeout2秒・各要求新規HTTP/1.1接続・自動retryなし。source Envoyの収束/実route weightsをread-onlyで確認後に各窓を開始。全窓の開始/終了時刻、総要求、v1、v2、HTTP失敗、輸送失敗、未知本文を数え、分母から失敗を落とさない。
E. splitの判断: n=2000で全件200/既知本文、v2が160～240（8～12%）、v1=n-v2を学習用の事前固定許容域とする。厳密な10%交互配分を期待しない。独立Bernoulli近似で標準偏差は約13.4要求、±40は約3σであり、公式保証ではない。境界外は失敗/調査、良い窓が出るまでの繰返しや許容差の後変更はしない。必要な再試験はManagerの残予算内で明示する。
F. restore: stable100:0を適用→clientのXDS収束と200要求全v1を確認→v2を0 replicaにしてEndpointSliceから外れ処理中要求がなくなったことを確認→**VS→DR**を除去。v2が残る状態でVSを先に消すとKubernetesの通常LBでv2へ流れ得る。次にrev labelを外して保存済みplain client/v1へ再作成し、proxyなし・DNS GET 200/v1を確認。非参加4 APIの同じGET smokeを再確認。
G. control teardownは別範囲: 残った注入Podとmesh依存がないことを確認し、今回の正確なinventoryのistiod/専用webhooks/RBAC等だけを順に除去。CRDはcluster共有なので通常保持。CRD削除は全Istio CR消失を伴い得るため独立の破棄承認なしに行わない。namespace/Secret/CAもUIDと所有一致が確認され、明示された破棄範囲のみ。cluster再作成/削除、既存DB/PVC/Secret削除は禁止。

この手順のHTTP復帰は既存mesh IssueのCRUD成功を証明しない。後続で既存APIへ導入するなら、その別スコープで既存IssueのCRUD等を実測する。今回の製品ゴールは手順を準備することであり、上記実行や実比率成功を準備Issueの完了前提に追加しない。

## 6. 実施権限と影響
| 区分 | 必要能力・影響 | 今回の状態 |
| --- | --- | --- |
| Research | repo/Issue/公式資料のread、design bytesをLibraryへ保存 | 認可済み。repo/clusterは変更しない |
| Cloud実装 | learning/istioだけのGit差分、image/chart取得・offline render/静的検査 | 後続assignment。今回未実施 |
| 将来のfoundation適用 | Namespace、CRD、ClusterRole/Binding、Mutating/ValidatingWebhookConfiguration、control namespace内Deployment/Service/SA/Role/Binding/ConfigMap、runtime CA Secret等の作成/patch | 現在未許可。namespace限定editorだけでは不足。既存で十分な権限を持つoperatorと正確なinventoryの承認が必要 |
| 将来の経路確認/復帰 | 専用namespaceのDeployment/Service/SA、VS/DR、namespace label、pods/exec、read/logs/status、所有fixtureの再作成/scale/delete | 現在未許可。対象とcleanupを限定した実施承認が必要 |

sidecar CNIを使わない案では、injectされたistio-initがroot/NET_ADMIN/NET_RAWを要求し得る。対象namespaceのAdmission/Pod Security条件が拒否するなら停止し、namespace PSA label・権限・policyを弱めない。CNI導入やambientへの変更は別設計判断。appのautomountServiceAccountToken=falseと、proxyに必要なprojected token/証明書の生成は別物であり、値を公開しない。

discoverySelectors/exportToは設定visibilityの絞込みで、RBACやNetworkPolicyによる隔離ではない。公式istiodはcluster-wide watch・Secrets読取り・ConfigMap更新等の権限を持ち、webhook/CRDもcluster-scoped。namespaceを絞っただけで「cluster権限不要」「他namespaceへの権限がない」とは主張しない。root CA ConfigMapを選択namespaceへ作成する挙動もinventoryに含める。既存ロールの無断付与/権限変更、Admission迂回は行わない。

## 7. Issueへの適合と小Issue案
- platform-gitops#8 は導入成功・非参加通信維持・解除/資源使用量の**runtime** Issue。本案は方式/version/opt-inと復帰設計を供給するが、#8を完了にしない。
- application-manifest#27 は2revision render・負fixture・同chart内復帰設定に近い。ただし所有repoはapplication-manifest、今回のDB非接続fixtureはplatform独立経路。関連capabilityとして示し、#27へ黙って移植/完了認定しない。
- application-manifest#28 と親#10 は実比率・復帰後CRUDを要求。手順の参考であり、今回のruntime実施やCRUD追加を要求しない。
- application-manifest#14 のRollouts、platform#19のmTLS強制、retry/circuit-breaker/faultは対象外。
- monitoring platform#83のlearning/monitoringとAccount opt-in、backend#50のOrderPaymentとmanifest共有ファイル所有を尊重。本案はどちらにもhard依存しない。

原本作成時の小Issue案（現在の追跡先はplatform-gitops#85）: **[Istio準備] 専用1経路の導入・90:10設定と確認手順をGitへ用意する**。
主repo platform-gitops、1つのlearning/istio差分、主validation layerはstatic contract＋独立Review。
ACは次の委任原文5文をそのまま使用し、runtime成功を追加しない。既存mesh Issueへの関連リンクと、実操作は別承認であることを本文に記す。原本作成工程ではIssue新設/編集/Close/PR作成を実施していない。現在の配送はIssue #85向けの設計派生版公開のみであり、製品実装やIssue完了を意味しない。

## 8. AC原文と最早判定段階
IDは照合のため原文順に付けた索引で、新しい機能ACではない。
| ID | AC原文 | 今回Researchの責務 / 製品の最早確認 |
| --- | --- | --- |
| AC-1 | 対象namespace・通信経路・導入方式・バージョンを固定する。 | 1～3で固定 / 設計と実装差分照合 |
| AC-2 | 導入設定と対象サービスのv1/v2への90:10振り分け設定をGitに用意する。 | 3の具体surface / 後続Git実装・統合 |
| AC-3 | 静的検証と独立レビューが通る。 | 4の必要検査確定 / 同じ実装候補の独立Test/Reviewと必要CI |
| AC-4 | 導入前後の疎通・実際の要求比率・元へ戻す方法を確認手順にする。 | 5で手順化 / 手順の検査。実行成功は今回の製品ACではない |
| AC-5 | クラスタへ適用する権限や影響範囲を明示する。 | 6で明示 / 設計とrender inventory照合 |

Implementation Ready: 本設計で限定実装を開始できる。実image/chart/CLI bytesの固定と静的検査、独立Reviewは後続責務。
Deployment Ready: 現在未評価・未許可。実target/access、clusterの現状、capacity/Admission、digest一致、競合不存在、mutation/cleanup範囲を別に確認する。
Researchの工程完了とLibrary配送は、Git実装完了・独立Review合格・製品準備完了・導入成功を意味しない。全製品ACのfinal_assessmentはManagerだけが行う。既存試行/失敗/残予算は保持し、今回のattempt1から履歴をリセットしない。
