# Istio の学習用準備（offline）

専用 namespace の準備、stable / 90:10 / plain の設定と offline 検証手順を保持する。
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
