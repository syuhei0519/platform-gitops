# 学習の記録

Issue と PR は「目的・やったこと・観察結果・学び」の4項目を基本にする。
未着手なら「未実施」と明記し、必要な AC と依存だけを目的に添える。

ログ、生成 JSON、render、attempt ごとの出力は CI artifact に保存する。
Git に残すのは対象 commit、再現コマンド、結論、未確認点を記した Markdown。
artifact は期限切れになるため、結論と再現手順を artifact へのリンクだけにしない。
実行に必要な schema、テスト fixture、lockfile、依存 vendor は通常のソースとして保持する。
image digest、依存固定、署名・真正性検査、security check の失敗拒否は維持する。
秘密値・内部ログ・非公開実行 ID は公開文書に含めない。

## 既存 Issue / PR の変換ルール

原文の履歴・失敗・未確認点は消さず、現在の結論と最終確認へのリンクを追記する。
Manager 判定、固定 bytes、配送 receipt、attempt 予算は通常の Issue / PR では必須にしない。
139 Issue の一括更新は行わない。実装前の Issue は未実施のままにする。

代表例: backend #50 と PR #51 は、premerge の未実施記録を保持して
「マージ後の最終確認」へリンクする。2026-10-18 07:31:25 UTC のローカル kind で
Order→Payment の成功201/再送200、拒否201/再送200、実 Payment GET との一致を確認済み。
[最終確認コメント](https://github.com/syuhei0519/backend-app/issues/50#issuecomment-6247220637)。
これは今回のタスクで再実行した試験ではない。既存 Issue / PR 本文の更新は Manager が担当する。

## 過去の生成報告

受入結果・権限試験などの生成 JSON (12件) は固定履歴へ整理した。
[当時の文書](https://github.com/syuhei0519/platform-gitops/tree/b2e8758ce933a56bddf2931b167f2f5892e65358/docs) から取得できる。
過去の成功・失敗の判定は変更していない。新規の結論と再現コマンドは Markdown に残す。
