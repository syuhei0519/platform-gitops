# CI の整理

## 目的

実行される GitHub Actions と再現手順を一致させ、使わない GitLab CI の処理を除く。

## やったこと

未使用 include と旧 registry 用の公開・取得スクリプトを削除した。
現在の release.py は GHCR / GitHub Releases 専用で、GitLab URL は拒否テストにだけ残す。
共有ツールの取得・配置は backend-app の共通 Action にまとめる。
旧動作はこの変更前の Git commit で追跡できる。

## 観察結果

既存の CI job 名、security checks、由来・digest・SBOM の拒否条件を維持する。
検証コマンドは [CI 文書](ci-documentation.md)。実行結果は今回の PR 本文に記録する。
実 cluster 操作、権限設定、Bot の導入や自動更新の開始は行わない。

`bash ci/github/test.sh` と `sh ci/validate.sh` は成功。
Runner / Argo の構成検証スクリプトは現在も使われるため保持した。
Renovate の設定・抽出検証は成功。実 Bot の運用、実 cluster 操作は未実施。

## 学び

OCI archive の checksum と image digest は異なる対象を識別するため、両者の照合が必要になる。
使われない互換処理を除くと、実際に公開・検証する形式と失敗条件を追いやすくなる。
