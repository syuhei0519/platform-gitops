# Core Platform v1 Issue索引

2026-10-01。19親Issueを作成、全てOpen。32内部作業単位は親本文内のチェックリスト。未完了をDONEにしない。

| PE | Issue | 状態 |
|---|---|---|
| PE-001 | [PE-001 現状と責任境界を固定する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/1) | Open / TODO |
| PE-002 | [PE-002 App of Appsの初回依存順序を検証する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/2) | Open / TODO |
| PE-003 | [PE-003 配備定義の全イメージ変更へ由来検証を適用する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/3) | Open / TODO |
| PE-004 | [PE-004 マージ直前の鮮度確認を接続する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/4) | Open / TODO |
| PE-005 | [PE-005 設定変更時の再配置を実証する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/5) | Open / TODO |
| PE-006 | [PE-006 Secret再注入とDB 認証情報の更新手順を確定する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/6) | Open / TODO |
| PE-007 | [PE-007 切り戻しとデータ保護を実証する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/7) | Open / TODO |
| PE-008 | [PE-008 CI構造と実行条件を整理する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/8) | Open / TODO |
| PE-009 | [PE-009 観測基盤の境界と予算を準備する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/9) | Open / TODO |
| PE-010 | [PE-010 Kubernetes メトリクスの収集を成立させる](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/10) | Open / TODO |
| PE-011 | [PE-011 Collectorの受信・処理・転送を成立させる](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/11) | Open / TODO |
| PE-012 | [PE-012 Go APIをOTel計装する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/12) | Open / TODO |
| PE-013 | [PE-013 2 ダッシュボードとNew Relic トレース閲覧を整備する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/13) | Open / TODO |
| PE-014 | [PE-014 観測障害と容量上限を実証する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/14) | Open / TODO |
| PE-015 | [PE-015 Trivy FS 検査と方針を導入する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/15) | Open / TODO |
| PE-016 | [PE-016 BuildKit出力を同一OCI 成果物へ変更する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/16) | Open / TODO |
| PE-017 | [PE-017 イメージ検査・SBOM・保存契約を実装する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/17) | Open / TODO |
| PE-018 | [PE-018 検査済みイメージだけを公開・提案する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/18) | Open / TODO |
| PE-019 | [PE-019 Core Platform v1の総合受入を実施する](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/19) | Open / TODO |

依存正本はIMPLEMENTATION_TICKETS.mdの作業単位DAG。PE-001はMRレビュー待ち、PE-002〜010は完了依存待ち。PE-011〜019は今回の実装対象外。frontend 016F→017F→018F→backend 016B→017B→018Bを維持する。
