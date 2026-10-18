# 学習専用 progressive delivery（導入案）

1環境・Account 1サービスの手動同期用Argo CD Coreとnamespace限定Argo Rolloutsです。
既存bootstrap/root/AppProject/Production登録/監視chartを変更しません。
Argo CD API server/UI/Dex/ApplicationSet/Preview、Meshは含みません。
manifest Issue #11の明示opt-in時だけ、Accountの固定HTTP結果によるinline AnalysisRunを許可します。
Application操作はkubectlで行い、新しいArgo API tokenは不要です。

## 固定版と描画

- Argo CD v3.5.2、source `e258ee23c3e52266d407572f4bcdfe7d9ed36cb5`。
  公式Coreからcontroller/repo-server/Redisと必要ConfigMap/Serviceだけを抽出します。
- Rollouts v1.10.1、source `356117c8e9a21b74b58c2c01d640aa91994522b5`。
  公式controllerに`--namespaced`を設定し、1 namespaceだけをwatchします。
- controller/Redisはdigest固定。公式入力2ファイルもSHA-256照合します（値は `render.py`）。
  download/renderはCloudのみ。大量のupstream schemaをGitへ複製しません。
- Argo CD用と既存アプリ用に、相異なる明示的`learning-*` namespaceが必要です。
  namespace自動作成・cluster作成・kubectl実行はscriptに含みません。Secret自体も生成・描画しません。

```bash
# Cloud、Python依存 PyYAML==6.0.3 jsonschema==4.26.0 lupa==2.6
python3 learning/progressive-delivery/validate.py --output /tmp/progressive-fixture
python3 learning/progressive-delivery/render.py \
  --namespace "$NAMESPACE" --control-namespace "$CONTROL_NAMESPACE" --owner "$OWNER" \
  --cache /tmp/progressive-cache --output "$OUT"
python3 learning/progressive-delivery/application.py \
  --namespace "$NAMESPACE" --control-namespace "$CONTROL_NAMESPACE" \
  --manifest-commit "$MANIFEST_COMMIT" --values "$ACCOUNT_VALUES" --output "$OUT/application.yaml"
```

`MANIFEST_COMMIT`はReview済みAccount chartを含む40桁SHA。Account valuesはmanifest側runbookを参照し、
既存のimage/source/owner/telemetry有効値を照合します。fixtureは架空imageなので実配備に使いません。
既定のApplication allowlistはこのrepoのRollout/Serviceのみ、destinationは当該namespaceだけ。
analysis enabled時の追加allowlistと権限は末尾を参照してください。
自動sync/selfHeal/prune、削除finalizerはありません。stable/canary Serviceのhash selectorだけを
ignoreDifferences + RespectIgnoreDifferencesで保護し、手動再同期でControllerの割当を壊さないようにします。
直接のoperation patchにも `sync.syncOptions: [RespectIgnoreDifferences=true]` を指定します。
manifest側runbookの通り、次のprofile変更/同期/解除前にoperationが空・phase終端であることを確認します。
通常syncはSucceededを要求し、意図したfailed試験のFailed/Errorは記録してから復帰します。

## 実環境への個別承認が必要な一覧（まだ適用していない）

| 出力/対象 | 追加・変更内容と影響 | 解除範囲 |
| --- | --- | --- |
| `crds.yaml` | cluster全体にApplication/AppProject/Rollout/Experiment/AnalysisRun/AnalysisTemplateの6 CRDs。Experimentはinformer用。AnalysisRun/AnalysisTemplateはIssue #11 opt-inで実行対象 | 他利用がないことを再確認してから6 CRDsのみ。削除は全namespaceの同kindを消すため別承認 |
| control namespace | 新規専用namespace。既存argocdや他環境を流用しない | 所有リソースを個別解除後、この新規namespaceだけ |
| `control.yaml` | SA 2個、Role/Binding、Core controller 1・repo-server 1・Redis 1、ConfigMaps/ClusterIP Services。secret作成Job・API server・cluster role・webhookなし | この出力内の所有資源のみ |
| `target.yaml` | 既存学習namespaceへSA/Role/Binding、namespaced Rollouts controller 1。RS生成/scale、stable/canary Serviceのhash patch、Pod metadata/Event更新、対象namespace内のleader-election Lease作成/更新 | この出力の資源だけ。namespace自体は消さない |
| 同Role | Argo CDは対象namespaceのRollout/Serviceを書込み、Pod/RS/Deploymentを読取り。Issue #11の追加Template/Run/provider権限は末尾参照。既定AppProjectはRollout/Serviceに制限 | 同Role/Bindingのみ。DB/Job/PVC/Secretのwrite権限はない |
| 通知用read | RolloutsのSecret readは固定名`argo-rollouts-notification-secret`のみ。watchは公式controllerがname fieldSelectorを付ける。Account DB Secretは読めない | Role/Bindingのみ。通知Secretは作成しない |
| 登録前提（render対象外） | ownerが別承認後にCoreの空settings Secret `argocd-secret`と、in-cluster登録Secret `learning-in-cluster`を供給。登録はname=in-cluster/server=https://kubernetes.default.svc/namespaces=対象namespace/clusterResources=false/config={}、label argocd.argoproj.io/secret-type=cluster。settings Secretはlabel app.kubernetes.io/part-of=argocd。tokenを含めず標準Pod SA credentialを使う | この2 Secretだけ。既存Secretへ上書きしない |
| Redis前提 | ownerが別承認のprivate手段で新control namespaceに`argocd-redis`/key `auth`を用意。scriptはpassword生成/Secret値取得をしない。controllerはcontrol namespaceのsettings/repository/cluster Secretを読む | 承認した新Redis Secretだけ。アプリSecretとは別 |
| `sample.yaml` | 独立サンプルRollout 1、Redis imageを永続化なしで起動。生成RS/Podによるcontroller受入専用 | サンプルRolloutをforeground削除。Account移行の証拠には数えない |
| `application.yaml` | AppProject/Application各1、既存Account公開Serviceのselector切替とAccount Rollout/補助Serviceのみを手動同期 | Applicationはfinalizerなしorphan削除。アプリ復帰手順を先に実行 |

この一覧は承認済み/導入済みの証明ではありません。SecretやRBAC/CRDをCloudから適用しません。
Redis Secretの供給が未承認ならCore起動だけを保留し、render/Reviewは進められます。
`argocd-secret` の空オブジェクトと `learning-in-cluster` の上記非秘密設定は、それぞれの個別承認後に
通常のlocal bootstrapで作成できます。CloudでのSecret生成/適用や、3 Secretの前提解消を意味しません。
Redis authはユーザーの安全な最終操作として、承認済みの秘密ファイルをローカルで用意し、shell traceを無効にして
`c create secret generic argocd-redis --from-file=auth="$REDIS_AUTH_FILE"` を実行します。
ファイルはユーザー管理のprivate領域に置き、値やSecret YAMLをチャット/ログへ出しません。
既存Secretがあれば上書きせず停止します。実装担当は認証情報を生成・入力・取得しません。
RBACはサービス間の完全なsecurity isolationを保証するものではありません。Controllerは対象namespace内のRSを管理できるため、
対象namespace/既存所有ラベルを再確認します。cluster-adminや既存AppProjectの権限緩和は不要です。

常駐4 Podのrequests合計は250m CPU/448Mi memory/128Mi ephemeral、limits合計は2CPU/1280Mi/1Gi。
サンプル追加は10m/16Mi（limit100m/64Mi）。Account10 replicasは500m/640Mi（limit5CPU/2560Mi）。
一時的なPod置換・initContainer、既存4API/4DB/監視とnode余力をローカルで確認します。
metrics-serverを新しい前提条件にしません。可用性保証のための冗長構成ではありません。

## Review後の導入と別々の受入（ローカル専用・未実施）

ローカルTest担当が最新のcontext/cluster/namespace/ownerとKubernetes versionを再確認し、上の対象へ個別承認を得てから進みます。
Secret値、kubeconfig、内部実行IDを公開ログへ出しません。既存CRD/同名資源があれば上書きせずManagerへ返します。
Argo CD/Rolloutsのimage到達性・Kubernetes 1.36.4とのruntime互換性は未検証です。
承認済み固定renderを使い、必要なkubectl Rollouts pluginもv1.10.1で揃えます。

```bash
k() { kubectl --context="$CONTEXT" --namespace="$NAMESPACE" "$@"; }
c() { kubectl --context="$CONTEXT" --namespace="$CONTROL_NAMESPACE" "$@"; }
# 個別承認後: 新control namespaceを作成。Redis auth Secretはownerがprivate手段で供給。
c create namespace "$CONTROL_NAMESPACE"
k apply --server-side -f "$OUT/crds.yaml"
k wait --for=condition=Established crd/applications.argoproj.io crd/appprojects.argoproj.io \
  crd/rollouts.argoproj.io crd/experiments.argoproj.io crd/analysisruns.argoproj.io crd/analysistemplates.argoproj.io --timeout=120s
# ownerが承認済みprivate手段で上の3 Secretを供給済みであることを確認（値を表示しない）。
c apply -f "$OUT/control.yaml"
k apply -f "$OUT/target.yaml"
c rollout status statefulset/argocd-application-controller --timeout=300s
c rollout status deployment/argocd-repo-server --timeout=300s
c rollout status deployment/argocd-redis --timeout=300s
k rollout status deployment/learning-rollouts --timeout=300s
```

1. **platform #7 controller受入**: 既存Account等Deploymentが継続Readyであることを先に確認。
   `k apply -f "$OUT/sample.yaml"` 後、サンプルのownerReferenceを持つReplicaSet生成とReadyを確認。
   サンプルPodTemplateにannotationを1つ追加し、更新pause/新RS生成を観測してpromote。
   `k delete rollout learning-rollouts-sample --cascade=foreground --wait=true` 後にサンプルRS/Podだけが消えたことを確認。
2. **manifest #26/#25/#12 Account受入**: manifest `learning/canary/README.md` の移行・pause・promotion・abort・復帰を実行。
   サンプル成功とは別に、Accountの10/50/100 status、image/source、CRUD/Pod置換後保存、Argo health、復帰後再同期を記録。

CoreはAPI serverを持たないのでArgo login/tokenは不要です。Applicationのoperationパッチによる手動syncと
`status.health`の取得を使います。CloudのLuaケース成功を実Application health成功に置き換えません。

全撤去は先にAccountをDeploymentへ復帰し、Applicationの進行中operationがないことを確認します。
サンプルも削除した後、`target.yaml`/`control.yaml`/新Secret・登録を所有UID確認付きで個別解除します。
既存学習namespace、DB/PVC/Secret/migration、他サービスと監視は残します。
CRD削除は全namespaceの他利用なし確認と個別承認が必要です。namespace一括deleteや既存Helm release uninstallを復帰方法にしません。

参考: [公式namespace限定Rollouts導入](https://argo-rollouts.readthedocs.io/en/stable/installation/)、
[公式Argo CD導入/Core](https://argo-cd.readthedocs.io/en/stable/operator-manual/installation/)。

## 共有guardの確認

旧固定共有Action `ba8b3d211ac3e497900abd6c6f44f45388e3801c` の `scan-gate` は、
公式CRD内の `hostNetwork`/`hostPID`/`hostIPC` のschema宣言も実Pod設定として拒否します。
`-check-render-only <render.pyの出力directory>` で `LOCAL-HOST-NAMESPACE` 12件を再現しました。
入力CRDは上記の固定公式bytesであり、実Podのhost namespace使用はありません。
新renderを既存 `.rendered` security経路へ含めています。今回Action参照を、schema宣言と実Pod設定を
区別する公開済み共有guard commit `96e5509757fbfb4bdc7e8b47876cdcc8c288142c` へ更新しました。
公開commitのtreeは元 `fa921cd2aace14f9052b79aa6dc6f37cb07dd274` と同一です。
同commitのscan-gateをbuildし、`-check-render-only <render.pyの出力directory>` で
同じ6 CRDsを含むfixture renderが成功、finding 0件を確認しました。guardのGoテストも成功しています。
再生成・guard除外・弱体化は行っていません。required CIと独立Review、実cluster受入は別途確認が必要です。

## manifest Issue #11: inline web analysisの追加境界（未適用）

`application.py` は任意の `analysis: {enabled: true, case: success}` を受け付けます。
caseはsuccess/failure/inconclusive/missing/timeoutだけ。URL/image/service/namespaceの上書きは入力に含めません。
既定は従来の手動Canary。enabled時だけAppProjectへAnalysisTemplate、ConfigMap、Deploymentを追加します。
AnalysisRunはRollout controllerが生成するため、Argo CDからsyncするallowlistへは追加しません。
Applicationの固定commit、手動sync/pruneなし、Service hash差分保護とfinalizerなしは維持します。
`revision=failed`とのanalysis併用はreadiness障害との混同を避けるため拒否します。

実際に追加するRole権限（既存対象namespaceのみ）:

| SA | resource | 今回追加するverb / 制限 |
| --- | --- | --- |
| learning-rollouts | argoproj.io/analysisruns | create/update/patch/delete。get/list/watchは既存 |
| learning-rollouts | analysisruns/status, analysisruns/finalizers | update/patch |
| argocd-application-controller | argoproj.io/analysistemplates | get/list/watch/create/update/patch/delete |
| argocd-application-controller | argoproj.io/analysisruns | get/list/watchのみ |
| argocd-application-controller | core/configmaps | get/list/watch/create。update/patch/deleteは`learning-account-analysis-provider`名だけ |
| argocd-application-controller | apps/deployments | create。update/patch/deleteは`learning-account-analysis-provider`名だけ。get/list/watchは既存 |

Kubernetes RBACのcreateはresourceNamesで制限できないため、create自体はこのnamespace内の当該kindに許可されます。
list/watchもcache用にnamespace内を対象にします。既存Account Deploymentや他名ConfigMapのupdate/patch/deleteは拒否します。
AppProjectのkind制限は名前の制約ではなく、完全なサービス間隔離の主張ではありません。
cluster role/ClusterAnalysisTemplate/Job provider、DB Secret/PVC/Jobへのwriteは追加しません。
RolloutsによるAnalysisTemplate参照は既存get/list/watchを使います。

`argocd-cm resource.inclusions` にAnalysisTemplate/AnalysisRun/ConfigMapを追加します。
Deploymentは既存inclusionです。追加CRD/controller/Secret/Argo API tokenはありません。
常駐fixtureはmanifest chartが所有し1 Pod、requests25m/32Mi、limits200m/64Mi。
同chartでsync-wave=-1を付け、provider Ready後にAccount更新へ進めます。

Cloudでの担当検証:

```bash
python3 learning/progressive-delivery/validate.py --output /tmp/progressive-analysis-fixture
```

固定upstream checksum/6 CRDs、5case Application/AppProject schema、Roleの実効許可と拒否、8 Lua healthケースを照合します。
同じrenderを既存共有scan-gateへ渡し、guard除外やrequired check名の変更は行いません。
CIの架空image/namespace renderは既存artifactに保存します。

local Test担当はManagerの対象context/namespace/owner・追加権限適用の確認後だけ、固定commitから再renderして
既存Roleの差分、resource inclusions、opt-in AppProject/Applicationを適用します。CRD再作成/Secret変更は不要です。
初回goodのstep省略は受入成功に数えず、更新の新hash/Runと五caseのRollout statusをmanifest README末尾の手順で記録します。
Failed/Errorはabort、Inconclusiveはpauseを観測し、負例の手動promoteは禁止です。
Cloudではapply、実SAの認可、image到達性、controller生成Run、次step進行/停止は未実測です。
