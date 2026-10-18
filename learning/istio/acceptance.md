# 将来の確認・復帰手順と影響境界

**未実施の手順**です。今回のGit準備はcluster apply、namespaceラベル変更、RBAC/Secrets操作を許可しません。
以下の記載は必要権限の説明と将来の操作案です。実適用・cleanupには別の明示承認と所有確認が必要です。
適用方式は保存済みraw YAMLの手動applyだけです。Helm install/upgrade、istioctl install、Operator、Argo自動同期を併用しません。

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

1. **before / plain**: `bootstrap/plain.yaml` と `rendered/plain.yaml` の所有計画だけを、別承認後に適用します。
   client/v1の2 Podにproxyがないこと、ServiceのEndpointSliceがv1のみであることを確認。
   clientからDNS経由でGET /versionが200/v1になることを確認します。
   非参加4 APIも、別途認可済みprobe経路で既存 `/livez` / `/readyz` GETを一度確認します。認可済み経路が不明なら推測せず後続担当へ確認。
   code/config/image、開始時刻、HTTP結果をprivate記録します。DB書込みは行いません。
2. **foundation**: `rendered/base.yaml` → 全15 CRDのEstablished確認 → `rendered/istiod.yaml` の順に適用します。
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

`evidence/resource-inventory.json` はbase+istiod+namespace+stableの**計画42リソース**です。
15 CRD、2 ClusterRole/2 ClusterRoleBinding、2 ValidatingWebhookConfiguration、1 MutatingWebhookConfiguration、2 Namespaceを含みます。
namespacedは2 SA、1 Role/1 RoleBinding、4 ConfigMap、2 Service、4 Deployment（istiod+fixture3）、VS/DR各1です。
`evidence/podspec-inventory.json` は3 Podのoffline注入結果であり、実Admission通過を証明しません。

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

Implementationの静的検証は `evidence/test-report.json`、独立Test/ReviewはManager別担当です。
同じ固定候補でschema・正負fixture・analyze・注入権限・復帰順序・公開安全性・所有境界と必須CIを確認してください。
AC-3の独立合格とIssue全体の最終判定は未実施です。今回runtime成功は求めず、将来操作は別承認です。
