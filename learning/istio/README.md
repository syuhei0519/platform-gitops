# Istio 専用1経路の準備 (#85)

このディレクトリは**準備用**です。root chart / bootstrap / Argo から参照しません。
設定をGitへ用意し、Cloudでoffline検証しました。クラスタ適用、namespace label変更、権限付与は実施していません。
独立Test/Reviewと同じheadの必須CIをManagerが確認した後に準備完了を判定します。
runtime成功はこのIssueの完了条件に含めません。

| 固定入力 | 値 |
| --- | --- |
| Istio / 方式 | 1.31.2 sidecar / 公式Helm chartのoffline描画YAMLを将来手動適用 |
| Helm / 計画Kubernetes | 4.2.4 / 1.36.4 (linux/amd64 imageのみ固定) |
| 制御 / 参加namespace | istio-learning-system / learning-istio-demo |
| revision | learning-1-31-2 |
| 経路 | 専用client → mesh-demo.learning-istio-demo.svc.cluster.local:8080 → v1/v2、GET /version |
| 応答 / mode | 厳密な `v1\n` / `v2\n`、default stable 100:0、split 90:10 |

名称はすべて計画入力であり、実環境識別子ではありません。Ingress、DB、Secret/PVC、既存APIの複製・patch、OrderPayment依存はありません。
fixtureは公式Python imageと標準libraryの1ファイルです。2つのserverは同一digestを使い引数だけでrevisionを区別します。
clientはidleで起動し、将来の明示操作で固定要求窓を実行します。

`versions.lock.json` は公式Istio release archiveとCLI、同archiveから抽出した未変更chart全ファイル、Helm実行binary、取得imageのplatform manifest/config digest・docker-save archive実測hash/sizeを固定します。
公式chart配布ホストはHTTP403だったため、取得できた公式release archive中のchartを `vendor/` に保持しました。chartホストのtgzを取得したという主張はしません。
Helm 4.2.4は、既存repoがCIに固定済みの第三者配布 `alpine/helm@sha256:76c375ee…` に由来します。
R1対応ではregistry index→linux/amd64 manifest→config digest→既存imageの各layer diffIDを検証し、`usr/bin/helm` の全bytesが使用binaryと一致することを確認しました。
配布source URL・size/hash・展開比較は `evidence/helm-provenance.json`、registryの元bytesは隣接する `helm-distribution-*.json` に保持します。
これは信頼済み既存インストール由来の証跡です。Helm公式publisherの配布imageや署名検証済みarchiveとは呼びません。get.helm.shのarchiveは以前のHTTP403により未取得のままで、拒否を迂回していません。
binary pinは64,274,594 bytes / SHA256 `92e191314f44aac173711bb0247c38c727f3ddf65ad16a01c0861d509a63a9e1` から変わらず、renderは再生成していません。
attempt 1の設定・Handoff・実測は `evidence/attempt-2-preservation.json` の固定commit/size/hashで保存し、attemptをリセットしません。
image archiveは非公開保持の観測bytesです。docker-save再実行で同hashになる保証はなく、再取得identityはregistry digestで確認してください。
imageの実cluster load / 実imageIDは未検証です。未知architectureに流用しません。

Kubernetes 1.36は公式対応表の**supported**範囲です。
`testedK8sVersions` は表の **Tested, but not supported** という別欄です。その列の有無から当該kind構成の実測を推定しません。
この実環境でIstioが動作検証済みとは主張しません。

## オフライン検証の再現

Pythonの `PyYAML==6.0.3`、`jsonschema==4.26.0` とlock一致のHelm/istioctl binaryを用意します。
以下はディレクトリ内からのコマンドです。`HELM` / `ISTIOCTL` は各自のローカルbinaryへのパスです。

```sh
python render.py --helm "$HELM" --istioctl "$ISTIOCTL" --output "$NEW_OUTPUT"
python validate.py --rendered "$NEW_OUTPUT" --helm "$HELM"
python fixture/test_fixture.py
diff -ru rendered "$NEW_OUTPUT"
```

`NEW_OUTPUT` は存在しない新規ディレクトリとします。render.pyはbinary/chart hashを確認し、KUBECONFIGを存在しない一時パスへ固定して描画・注入・`analyze --use-kube=false --failure-threshold Warning` を実行します。kubectlを呼びません。
validate.pyは当該経路の限定検証器です。公式CRDのv1 spec schemaに加え、既知objectの未知fieldを拒否するローカル制約を使用します。
全Kubernetes kindの汎用schema検証器ではありません。Service/Deployment/Namespace等は明示した狭い契約を検査します。
正3/負17のfixture、未知mode・plain+split・placeholder image・namespace逸脱のHelm拒否、注入PodSpec、RBAC/webhook inventoryを確認します。
`--write-inventory` は `evidence/` の安全な構成inventoryを更新するローカルオプションです。
既存CIは変更しておらず、独立Testはこの限定検証も同じ候補で実行してください。

## 配置

- `bootstrap/`: namespace計画。plainにはrev labelなし。既存labelの除去はこのファイルのapplyだけでは行えません。
- `install/`、`vendor/`、`rendered/base.yaml` / `istiod.yaml`: 公式導入values・未変更chart・固定描画。
- `chart/`: 専用Service/SA/ConfigMap/client/v1/v2とVS/DR。plainではv2/VS/DRを生成しません。
- `rendered/stable.yaml` / `split.yaml` / `plain.yaml`: 将来適用候補。`injected.yaml` は検査専用の注入後PodSpecで、namespace自動注入と併用applyしません。
- `evidence/`: 静的検証結果、構成・権限inventory、取得registry index bytes。運用ログ・秘密値は含みません。
- `acceptance.md`: 将来の疎通・比率確認・復帰と権限境界。

## 固定出典

採用設計は[固定Research派生版](https://github.com/syuhei0519/platform-gitops/blob/043b75698e1b6a47513eb952e906f42f0baaffd6/docs/artifacts/istio-research-20261018/design.md)と[公式出典inventory](https://github.com/syuhei0519/platform-gitops/blob/043b75698e1b6a47513eb952e906f42f0baaffd6/docs/artifacts/istio-research-20261018/sources.json)です。
Managerの別認可の下でSystem正式Consumer CLIにより3 payloadの全bytes、manifest/publication、承認pinを検証しました。Consumption Receiptは非公開保持です。
[公式Istio source](https://github.com/istio/istio/tree/e2f3a7949a358af7dd901a6f9ccd6ecdd7270271/manifests/charts)と[公式Helm source](https://github.com/helm/helm/tree/3900f434fd3ef2b84065dc04508df48f288dba00)を固定参照します。

関連するplatform-gitops#8、application-manifest#10/#27/#28のruntime/CRUD ACは完了扱いにしません。
独立Test/Review、Merge Ready、Deployment Ready、Issue最終判定は別です。
