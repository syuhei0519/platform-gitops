# 共有 CI ツール

正本は [backend-app/tools](https://github.com/syuhei0519/backend-app/tree/6f53affcdcad6f270eee2f205915d22fe0145e9c/tools)。
固定 commit: `6f53affcdcad6f270eee2f205915d22fe0145e9c`。

repo root で `bash ci/shared-tools.sh` を実行すると必要な Go module を `tools/` に取得する。
生成したディレクトリは Git 対象外。再取得時はそのディレクトリだけを削除する。
CI は取得失敗・既存ディレクトリとの混在を拒否し、通常の Go 依存固定と security checks を維持する。
`bash ci/github/test.sh` は取得・共有回帰テスト・repo 固有テストをまとめて実行する。

共有元 PR の commit 公開後に consumer CI を開始し、共有元を先にマージする。
共有元の commit を保持しない squash / rebase merge を使った場合は consumer pin をマージ後 SHA に更新する。
GitLab の API / registry 互換関数は過去テスト用。旧 trusted-consumer の Git tree snapshot は旧 commit で再現する。
新しい GitHub の required checks と trusted manifest verification は維持する。
