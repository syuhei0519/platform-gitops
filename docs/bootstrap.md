# Core Platform v1 bootstrapの責任境界

設計正本は [Design Freezeフォルダ](https://drive.google.com/drive/folders/1vihXtD4OlHAYIQ--gpC-iReK5hw25G8P) のDESIGN.md / IMPLEMENTATION_TICKETS.md / REVIEW_RESOLUTION.mdです。設計凍結は実装・AT完了を意味しません。

対象はsyuhei-platform-engineering-lab配下のfrontend-app / backend-app / application-manifest / platform-gitopsと、個人所有の `kind-platform-lab` です。旧frontend / backend / application-gitopsを混ぜません。

## 定義元と稼働値

- 固定版台帳の正本: 本repositoryの `bootstrap/versions.lock.yaml`。各アプリの `ci/tool-versions.env` は現在のCI実行契約です。PE-001の[基準記録](implementation/PE-001-baseline.md)は導入時の履歴として保持し、最終版では各保護mainと台帳・実ジョブ引数を照合します。
- Namespaceの唯一の定義元: `bootstrap/namespaces.yaml`。運用者によるbootstrap適用で所有し、Chart内NamespaceやCreateNamespaceを追加しません。
- Application / AppProject: 本repository。account Chart / SQLはそれぞれapplication-manifest / backend-app。Secret値はGit外で運用者が所有します。
- `charts/root/values.yaml` の `workloadsEnabled: false` は初期既定値です。`environments/local/root.yaml` の `true` は準備済みローカル環境の明示上書きです。

## 新規構築の有効化ゲート

kind/Argo固定版とNamespace、Runner foundationの権限・参照Secret契約を確認します。Secret作成/rotationは利用者承認後に運用者が実施します。初回imageのsource/build/digest由来とSecret再注入が成立してからworkloadsEnabledを有効化します。DB→migration→APIの対象revisionの準備完了待機はPE-002、Secret契約はPE-006で受入します。現在Healthyであることを新規bootstrap試験の代用にしません。

PE-009/010のPrometheus、PE-011〜014のCollector/New Relic/Grafanaは実装済みです。PE-018では両サービスを検査済みOCI・scan run・SBOMに結合した配信へ切り替えました。PE-019の新kind・最終同版負荷と全19件の完了は別の受入です。[再構築手順](operations/final-rebuild.md)と各実受入索引を使い、導入済みであることと最終受入完了を区別します。

## 実装ゲート

R01: 秘密なしの信頼済み固定render/inventoryと認証付き由来照合を分離。R02: digestとscan runを分離した証跡。R03: frontend全工程切替後にbackendへ切替。R04: API生成元のinstance/versionをCollectorで上書きしない。R14: 台帳パスを上記正本へ統一。これらを独自方式へ変更しません。

全作業単位の完了依存は19件の[Issue一覧](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items)と正本の依存DAGを参照します。未統合依存を完了扱いにせず、mainへのマージは利用者承認後です。
