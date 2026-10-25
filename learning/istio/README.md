# Istio の学習用準備（offline）

Issue8 は sidecar 方式、control `istio-learning-system`、opt-in `learning-istio-demo` の1 namespace、
revision `learning-1-31-2` に固定する。既存lockの Istio 1.31.2 / Helm 4.2.4 / K8s 1.36 と
pilot/proxy/fixture digestを再利用する。既存chartの `mode=stable,phase=plain` で
client/v1のみを描画し、namespace revision labelで自動注入する。
`issue8.yaml` はplain fixtureと同じ内容で、v2・VS/DR・90:10・全体mTLS強制は追加しない。
既存stable / 90:10 / plain の準備と検証は後続Issue向けに保持する。
この変更は Istio の新規導入や cluster 操作を行わない。実測済み範囲と未実施範囲は
[受入記録](acceptance.md) を参照する。過去の失敗と attempt は [履歴索引](evidence/README.md) に残る。

## 再現

`versions.lock.json` の Istio / Helm 版に合う binary を用意する。
通常の tool binary checksum、公式配布元情報、image digest、vendor と chart version を保持する。
Helm の layer 単位の provenance と registry index bytes、docker-save hash/size の二重保存は要求しない。
Helm は従来の固定 alpine/helm 配布物であり、公式 upstream binary との同一性を主張しない。

```sh
cd learning/istio
python render.py --helm "$HELM" --istioctl "$ISTIOCTL" --output /tmp/istio-render
python validate.py --rendered /tmp/istio-render --helm "$HELM"
python -m unittest discover -s fixture -v
```

render は新しい出力ディレクトリへ生成し、Git にコミットしない。
CI artifact に生成物を置き、PR にコマンド、対象 commit、結論、未確認点を記す。
`--write-inventory` は指定 render ディレクトリへ検査結果を出力する。
公式 CRD schema、namespace / security / image の境界、異常入力の拒否は維持する。
手動注入後の injected.yaml は検査専用であり、namespace 自動注入との併用 apply はしない。

## Issue8の限定出力

上のrender/validateを実行すると、`namespaces.yaml` + `base.yaml` + `istiod.yaml` +
`issue8.yaml` が計35資源になる。`plain-namespaces.yaml` と `plain.yaml` は導入前/解除後用。
`--write-inventory` の `issue8-resource-inventory.json` と `issue8-podspec-inventory.json` で
RBAC/所有計画と2 Podのoffline注入結果を確認できる。`issue8-injected.yaml` は検査専用。
実機のHealthy、非参加通信、解除、資源使用量は未実施。
[Issue8受入手順](acceptance.md#issue8の限定受入未実施)を次工程で使用する。

## 構成と入口

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [argocd-cache-test/](argocd-cache-test) | 固定版 Argo CD の cache 範囲を fake client で確認する Go テスト。 |
| [bootstrap/](bootstrap) | 学習用 namespace と比較用の初期定義。 |
| [chart/](chart) | Istio の通信比率を確かめる試験アプリを描画する Chart。 |
| [evidence/](evidence/README.md) | 過去の検証記録への索引。稼働状態の現在値を示すものではありません。 |
| [fixture/](fixture) | 通信試験サーバーの集計・応答を確認するテスト。 |
| [fixtures/](fixtures) | 経路や権限の検査で使う固定した試験入力。 |
| [install/](install) | 上流 Istio Chart に渡す値。revision や対象範囲を固定します。 |
| [rendered/](rendered/README.md) | 過去の描画結果への索引。新しい描画結果は検査時に作ります。 |
| [vendor/](vendor) | 固定した上流 Chart と CRD。外部依存なので直接書き換えません。 |

### 本文にコメントを入れないファイル

JSON などの形式、埋め込み・checksum、既存 reader の文字コードの前提を保つため、役割はここで説明します。

| ファイル | 役割 |
| --- | --- |
| [bootstrap/namespaces.yaml](bootstrap/namespaces.yaml) | Istio の学習用 control plane と試験 workload の namespace を用意する定義です。 |
| [bootstrap/plain.yaml](bootstrap/plain.yaml) | sidecar を付けない比較用 namespace を用意し、通信経路の違いを確かめるための定義です。 |
| [chart/files/demo.py](chart/files/demo.py) | Istio の通信試験で版や失敗を返す HTTP サーバーです。Chart が ConfigMap にそのまま埋め込みます。 |
| [chart/values.schema.json](chart/values.schema.json) | Istio の通信比率を確かめる試験アプリに渡す設定値の必須項目・型・許可する値を検証する schema です。 |
| [install/istiod.values.yaml](install/istiod.values.yaml) | 固定版 Istio control plane に渡す設定です。学習用 revision と対象範囲を限定します。 |
