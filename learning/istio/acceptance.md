# 将来の確認・復帰手順と影響境界

**未実施の手順**です。今回のGit準備はcluster apply、namespaceラベル変更、RBAC/Secrets操作を許可しません。
以下の記載は必要権限の説明と将来の操作案です。実適用・cleanupには別の明示承認と所有確認が必要です。
適用方式は保存済みraw YAMLの手動applyだけです。Helm install/upgrade、istioctl install、Operator、Argo自動同期を併用しません。

## Issue8の限定受入（未実施）

Issue8の現在の対象はこの節。以下の広いrouting手順は既存prep履歴として保持し、今回実行しない。
sidecarのみ、control `istio-learning-system`、参加 `learning-istio-demo`、revision `learning-1-31-2`。
client/v1だけを使用し、v2/VS/DR、全namespace参加、Traffic Policy完成、mTLS全体強制は対象外。
既存API/DB/PVC/Secret/migration、既存Argoのcluster権限は変更しない。

### 固定ソースと所有・停止条件

実施担当は承認されたGit commit SHAを明示してcheckoutし、READMEの固定tool検証付きrender/validateを実行する。
render後の35資源のkind/name/namespaceと、開始前の不存在または所有UIDを照合する。
元から同名資源（CRD・default validatorを含む）がある場合は所有が計画と一致することを確認できるまで停止し、
上書きや借用をしない。context/cluster/API接続先、node capacityとimage architectureもprivate入力と照合する。
以下のコマンドは将来の承認済み実施担当用で、このCloud作業では実行しない。

```sh
: "${ISTIO_CONTEXT:?verified context is required}"
# 所有/接続先照合の後だけ。保存済みraw YAMLを手動applyする。
kubectl --context "$ISTIO_CONTEXT" apply -f /tmp/istio-render/plain-namespaces.yaml
kubectl --context "$ISTIO_CONTEXT" apply -f /tmp/istio-render/plain.yaml
```

### 導入前 → mesh → 解除後

1. **導入前**: client/v1両Podのproxy/init不在、v1だけのEndpointSlice、DNS経由GET `/version` の
   plain窓200件すべて200/v1を確認する。非参加Workloadは既存の認可済み経路でread-onlyの
   `/livez` / `/readyz` を確認し、Pod UID・image/config・proxy不在も記録する。経路不明なら停止する。
2. **導入**: 所有照合済み `base.yaml` をapplyし15 CRDのEstablishedを確認後、`istiod.yaml` をapplyする。
   istiodのReady、Service/EndpointSlice、revisionのvalidator/injectorを確認する。
   `namespaces.yaml` のapplyで専用namespaceだけをopt-inし、`issue8.yaml` をapplyする。
   内容がplainと同じなので、applyだけでは既存Podは変わらない。所有fixtureのclient/v1だけを
   rollout restartしReadyを待つ。Pod opt-in、legacy/ambient labelを追加しない。
3. **mesh受入**: 2 Podでfixture+proxyとinit、lockと一致する実imageID、proxy ReadyとXDS syncを確認する。
   clientのsource Envoy経由でDNS GETのstable窓200件すべて200/v1、失敗/未知本文0を確認する。
   istioctlのproxy-statusとproxy-config endpointsで対象service/endpointが同期していることも確認し、
   到達しただけでmesh経路Healthyとは扱わない。同じ非参加通信とproxy不在を再確認する。
4. **解除**: 所有namespaceのrevision labelを明示除去し（YAML省略では除去できない）、plain.yamlを再applyし、
   client/v1だけをrollout restartする。2 Podのproxy/init不在、v1だけのEndpointSlice、
   DNS plain窓200件200/v1と非参加通信の維持を確認する。
5. **control撤去**: 残存注入Podや他mesh依存がないことを確認後、保存inventoryと所有UIDに一致する
   istiod/Service/SA/ConfigMap/Role/Binding、専用revision webhook/ClusterRole/Bindingを撤去する。
   default validatorも今回新規所有かつ他利用がないと確認できる場合のみ対象とする。
   `kubectl delete -f base.yaml` は使用しない。15 CRDは共有schemaと他Istio CR消失の危険があるため残置し、理由を記録する。
   namespaceも残置する。fixtureの撤去は通信確認後、別途認可された所有範囲のみで行う。

```sh
# mesh中は warmup → stable、導入前/解除後は warmup → plain を実行する。
kubectl --context "$ISTIO_CONTEXT" -n learning-istio-demo exec deployment/mesh-demo-client -c fixture -- python3 -B /fixture/demo.py measure warmup
kubectl --context "$ISTIO_CONTEXT" -n learning-istio-demo exec deployment/mesh-demo-client -c fixture -- python3 -B /fixture/demo.py measure stable
# 解除時のlabel除去（所有一致確認後）
kubectl --context "$ISTIO_CONTEXT" label namespace learning-istio-demo istio.io/rev-
```

### request/limitと既存kind CRIによる資源記録

| 対象 | request CPU / memory | limit CPU / memory |
| --- | --- | --- |
| istiod 1 | 500m / 512Mi | 1000m / 1024Mi |
| fixture 2 | 各25m / 32Mi | 各200m / 64Mi |
| proxy 2 | 各100m / 128Mi | 各500m / 256Mi |
| 合計 | **750m / 832Mi** | **2400m / 1664Mi** |

各initも100m/128Mi（limit500m/256Mi）。逐次initのmaxと通常container合計、Pod overhead等のscheduler条件を照合する。
これは設定予算であり実使用量ではない。metrics-serverや新しい監視componentは追加しない。
導入前、mesh中、proxy解除後、control撤去後に同一の時間窓・既存kind nodeからCRI statsを取得する。
認可済みnode名だけを `ISTIO_KIND_NODE` に設定し、各窓の開始/終了（例30秒間隔）を記録する。

```sh
: "${ISTIO_KIND_NODE:?verified owned kind node is required}"
# 各窓の2点で採取。出力は認可されたprivate領域へ保存しGitへ置かない。
docker exec "$ISTIO_KIND_NODE" crictl stats -o json
```

全nodeでcontainer IDをPod/namespace/container名へ対応付け、各2点の差分から
`CPU m = delta(cpu.usageCoreNanoSeconds.value) / delta(timestamp ns) * 1000`、
`memory Mi = memory.workingSetBytes.value / 1048576` を集計する。
mesh fixture/proxy/istiodの内訳と非参加Workload、node全体との差分を同じ窓で記録する。
再起動でID変更・counter reset・欠測があればその窓を明記し、0使用量と扱わない。
撤去済みcontainerは不在として記録する。公開Markdownには時刻・窓・集計値・結論と残置理由のみ記し、内部ログや非公開実行IDを載せない。

### 正確な追加影響とCA資源

35資源は15 CRD、2 Namespace、ClusterRole/ClusterRoleBinding各1、ValidatingWebhook2、MutatingWebhook1、
SA2、Role/RoleBinding各1、ConfigMap4、Service2、Deployment3（istiod+client/v1）。
baseとistiod両方の `global.enableReaderRBAC=false` によりreader Role/Binding/SAは追加しない。
生成inventoryの全rules/subjectsを実施前に確認する。istiodの公式ClusterRoleにはcluster全Secretのget/list/watch、
ConfigMap create/get/list/watch/update、discovery/Istio/Gateway API等の読取・status更新や一部CRUD、
webhook更新、tokenreviews/subjectaccessreviews createが残る。namespace RoleにはCA Secretの
create/get/watch/list/update/delete、Gateway create、ConfigMap delete、lease get/update/patch/createが残る。
Gateway deployment controller無効化はこれら権限の縮退を意味しない。
initはUID0・NET_ADMIN/NET_RAW、proxyはUID1337、audience `istio-ca` のprojected tokenを使用する。
PSA/Admission等に拒否されたら停止し、既存保護を弱めない。
operatorには上記cluster資源とcontrol/fixture資源のcreate/patch、所有fixture再作成、read/status/execと
認可範囲の撤去権限が必要。既存Argoへこれらcluster権限を付与しない。

istiodが新規生成するCA Secret、root CA ConfigMapはrenderの35資源には含まれない。
既存Secretは参照内容の取得/変更/削除をせず、CAも値を公開しない。新規CAは存在・所有metadataのみ確認し、
**自動削除せず残置**して理由を記録する。CA Secret/namespaceの破棄は別の明示された所有・削除範囲に従う。
root CA ConfigMapは今回新規所有かつ依存なしと確認したものだけ撤去対象にできる。
Secret読取権限があることと、operatorにcredential削除を許可することを混同しない。

Issue8の3 AC（Control Plane/選択mesh Healthy、非参加通信維持、解除/実資源使用量記録）はすべて実機未確認。
独立Test/Reviewと実機承認を経て別途判定し、このDraftだけでIssue8を完了扱いしない。

## Cloud静的確認（2026-10-18）

基点 `e770689b859de437b8a097bdfebf4d3ba6b9610f`。固定lock、vendor、既存アプリ/Argo設定は変更しない。
READMEのrender/validate、`python3 -m unittest discover -s learning/istio/fixture -v` により、
35資源境界、固定tool/version/digest、Issue8の2 PodSpec、既存20+追加4契約fixture、
異常Helm入力4件拒否、既存fixture試験5件、offline analyze4経路を確認した。
Kubeconform v0.7.0（既存CIの固定digest）、strict/K8s1.36.0により、base/control/namespaces/plain-namespaces/
issue8/issue8-injectedの42文書中27標準資源がValid、15公式CRD定義はschema未提供のため明示skip。
VS/DR specは既存の公式CRD schemaによる正負fixture検証を維持する。
コンテナ内の証明書不足はホストの信頼済みCA bundleをread-onlyで渡して解消し、TLS検証は無効化しなかった。
render生成物・注入PodSpec・詳細inventory・ログはGitへ保存しない。
実機AC、実Admission、実imageID、CRI資源使用量、独立Test/Reviewは未確認。

## 既存routing prep（後続Issue向け・今回対象外）

## 開始前の停止条件

認可されたローカル実行環境で、private入力に正しいcontext/cluster/API接続先、operator、許可された操作・inventory、cleanup境界を固定します。
既定contextを使いません。すべてのkubectl/istioctl観測・操作には明示contextを指定します。
context名の一致だけで接続先一致を推定しません。version、namespace不存在または所有UID、既存mesh/同名CRD/webhook/RBACの競合、image/load identityをread-onlyで確認します。
競合は上書き・借用せず停止。cluster再作成、既存DB/PVC/Secret削除は禁止です。

node capacity、Admission / Pod Security、initのroot・NET_ADMIN/NET_RAW、proxy projected token/CA、image architectureを確認します。
拒否されたら停止し、namespace PSA label、RBAC、Admission、policyを緩めません。CNI/ambientへの切替は別設計です。
要求予算はistiod 500m/512Mi、fixture3個は各25m/32Mi、proxy3個は各100m/128Mi。
initは各100m/128Miで、通常containerとのmaxやpod overhead等を含む実scheduler条件を確認します。値は性能実測ではありません。

## plain → stable → 90:10 → stable → plain

[README の再現手順](README.md#再現)で `/tmp/istio-render` に生成した YAML を使います。
以下の生成 YAML のファイル名はこのディレクトリを基準にします。inventory も必要な場合は、
`learning/istio` で `python validate.py --rendered /tmp/istio-render --helm "$HELM" --write-inventory` を実行します。

1. **before / plain**: `bootstrap/plain.yaml` と `/tmp/istio-render/plain.yaml` の所有計画だけを、別承認後に適用します。
   client/v1の2 Podにproxyがないこと、ServiceのEndpointSliceがv1のみであることを確認。
   clientからDNS経由でGET /versionが200/v1になることを確認します。
   非参加4 APIも、別途認可済みprobe経路で既存 `/livez` / `/readyz` GETを一度確認します。認可済み経路が不明なら推測せず後続担当へ確認。
   code/config/image、開始時刻、HTTP結果をprivate記録します。DB書込みは行いません。
2. **foundation**: `/tmp/istio-render/base.yaml` → 全15 CRDのEstablished確認 → `/tmp/istio-render/istiod.yaml` の順に適用します。
   istiod ready、revision webhook/Service、CA配布を確認。非参加Podが新規injectされていないことも確認します。
   cluster共有のdefault validator/CRDも含むためnamespace限定の操作ではありません。
3. **mesh stable**: 参加namespaceだけに計画revisionを付け、client/v1を再作成。legacy/ambient labelを併記しません。
   proxy ready・実imageID（lockのlinux/amd64 config digestと比較）・XDS syncを観測します。
   **v2を作成する前に**stableのDR→VSを適用し、client Envoyのroute weights 100:0の収束を確認します。
   stable.yamlの一括applyだけではこの順序を保証できないので、将来operatorはkind/nameでDR/VSを抽出し先行適用します。
   その後v2を追加してReady/EndpointSliceを確認します。常にclientのsource Envoy経由で計測し、backendへのport-forwardは用いません。
4. **計測**: 各窓の前にXDS収束と実route weightsをread-onlyで確認し、`warmup`を別に実行します。
   stable窓200要求 → split.yamlのVS(90:10)反映・収束 → split窓2000要求 → stableのVS反映・収束 → stable窓200要求の順に測定します。
   DR/他設定の変更を比率以外へ広げません。`/livez`や未知pathは測定へ混ぜません。
5. **HTTP routing解除**: 最後のstable窓が全v1と確認できてからv2を0 replicaにし、EndpointSliceからv2が消え、処理中要求がないことを確認します。
   その後**VirtualService → DestinationRule**の順に解除します。v2がService対象に残る間はVSを消しません。
6. **plain復帰**: 所有参加namespaceからrev labelを明示的に除去し、保存済みplain client/v1へ再作成します。
   namespace YAMLでlabel項目を省くだけでは除去になりません。残存v2 Deploymentも承認済みfixture cleanup範囲で削除します。
   PodSpecにproxy/initがないこと、Serviceのv1だけのEndpointSlice、DNS GET 200/v1とplain窓を確認します。
   同じ非参加API smokeも再確認し、before/afterのimage/configと結果をprivate記録します。

将来の計測コマンド例（この文書では実行していません）。contextはprivate入力で検証済みの値を必須とします。

```sh
: "${ISTIO_CONTEXT:?verified private context is required}"
kubectl --context "$ISTIO_CONTEXT" -n learning-istio-demo exec deployment/mesh-demo-client -c fixture -- python3 -B /fixture/demo.py measure warmup
kubectl --context "$ISTIO_CONTEXT" -n learning-istio-demo exec deployment/mesh-demo-client -c fixture -- python3 -B /fixture/demo.py measure stable
# route weights 90:10の収束確認後
kubectl --context "$ISTIO_CONTEXT" -n learning-istio-demo exec deployment/mesh-demo-client -c fixture -- python3 -B /fixture/demo.py measure split
# 復帰100:0の収束確認後stable、proxy除去後plainを同じ形式で実行
```

concurrency1、timeout2秒、各要求新規HTTP/1.1接続、retry/redirectなし。
JSONは開始/終了・total・v1・v2・HTTP失敗・輸送失敗・未知本文を記録します。**分母は全要求**です。
stable/plainは200件すべてv1、v2/失敗/未知本文0。splitは2000件すべて200/既知本文、v2=160～240、v1=2000−v2を合格域とします。
8～12%は事前固定の学習用許容差で公式保証ではありません。失敗を除外した分母や、成功窓が出るまでの繰返し、事後の閾値変更は不可。
不合格では観測を保全しManagerが原因と残予算を判断します。stableへの緊急復帰自体も事前に認可範囲へ含めます。

## 必要権限と生成inventory

`/tmp/istio-render/resource-inventory.json` はbase+istiod+namespace+stableの**計画38リソース**（reader RBAC無効化後。旧計画40の履歴はGit参照）です。
15 CRD、1 ClusterRole/1 ClusterRoleBinding、2 ValidatingWebhookConfiguration、1 MutatingWebhookConfiguration、2 Namespaceを含みます。
namespacedは2 SA、1 Role/1 RoleBinding、4 ConfigMap、2 Service、4 Deployment（istiod+fixture3）、VS/DR各1です。
`/tmp/istio-render/podspec-inventory.json` は3 Podのoffline注入結果であり、実Admission通過を証明しません。

| 将来の担当 | 必要な既存権限と影響 |
| --- | --- |
| foundation operator | Namespace/CRD/ClusterRole/Binding/Webhookのcreate/patch、制御namespace内SA/Role/Binding/ConfigMap/Service/Deploymentのcreate/patch。現行権限で不足なら停止。権限の付与/昇格をこの手順で行わない |
| fixture operator | 専用namespaceのSA/ConfigMap/Service/Deployment/VS/DR操作、Pod再作成/scale/delete、namespace revision label、read/status/EndpointSlice、pods/exec。cleanup対象と所有UIDを別認可で固定 |
| istiod runtime SA | 公式Role/ClusterRoleの全rules/subjectsはinventory参照。cluster-wide Secret読取り、ConfigMap作成/更新、各種watch、webhook更新、tokenreviews/subjectaccessreviews、Gateway API等のstatus権限も残る。gateway deployment controllerを無効にしてもRBACは縮退しない |
| data plane | istio-initはroot/NET_ADMIN/NET_RAW、proxyはUID1337。appのSA automount=falseでもproxyにはaudience istio-caのprojected tokenがある。実token/CA秘密値を公開しない |

`discoverySelectors` / `exportTo` は設定visibilityを絞る機能で、RBAC/NetworkPolicyによる隔離ではありません。
公式webhookはrevisionをnamespace **またはPod** でopt-inでき、名前だけで当該namespaceへhard隔離されません。
この計画では専用namespace以外へrevision labelを付けず、Pod opt-inも追加しません。default revision向けwebhook/CRDのcluster共有影響も確認対象です。
istiodが生成するCA Secret、選択namespaceへのroot CA ConfigMap配布はrender時inventoryに現れないruntime資源です。値を取得/保存せず、存在と所有のみprivate確認します。
今回はnamespace限定editorで適用できるという主張をしません。既存roleの変更や付与も実施していません。

## control planeの撤去は別の破棄範囲

HTTP plain復帰とは分けます。残存注入Pod/mesh依存がないことを確認後、承認済みinventoryとUIDに一致するistiod/専用webhooks/RBAC等だけを撤去します。
CRDは原則保持。CRD削除は全Istio CR消失を伴い得るため別の明示破棄承認なしに行いません。
namespace/CA/Secretも独立した所有・削除範囲承認が必要です。共有default validatorを他meshごと消しません。
未許可の削除・cluster再作成、既存DB/PVC/Secret削除、権限やAdmission設定変更は行いません。

## ACと次工程

1. 対象namespace・通信経路・導入方式・バージョンを固定する。
2. 導入設定と対象サービスのv1/v2への90:10振り分け設定をGitに用意する。
3. 静的検証と独立レビューが通る。
4. 導入前後の疎通・実際の要求比率・元へ戻す方法を確認手順にする。
5. クラスタへ適用する権限や影響範囲を明示する。

過去のImplementation静的検証は [履歴索引](evidence/README.md) から参照できます。独立Test/ReviewはManager別担当です。
同じ固定候補でschema・正負fixture・analyze・注入権限・復帰順序・公開安全性・所有境界と必須CIを確認してください。
AC-3の独立合格とIssue全体の最終判定は未実施です。今回runtime成功は求めず、将来操作は別承認です。
