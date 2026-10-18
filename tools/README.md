# 共有 CI ツール

`.github/workflows/ci.yml` は backend-app の共通 Action を固定 commit で読み込み、
必要な module 名を `modules` で宣言する。取得処理のコピーと別の取得用 SHA は置かない。
GitHub Actions が固定した Action のコードを使い、既存ディレクトリへの混在を拒否する。

ローカルでは [backend-app の手順](https://github.com/syuhei0519/backend-app/blob/main/tools/README.md) に従い、
workflow と同じ commit を checkout して `ci/materialize-tools.sh` で配置してから `bash ci/github/test.sh` を実行する。

Renovate の設定は [固定参照の更新](../docs/shared-tools-updates.md) を参照する。
