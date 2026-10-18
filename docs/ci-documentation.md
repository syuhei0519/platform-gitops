# 現在の CI

入口は `.github/workflows/ci.yml`。GitHub Actions の比較元 commit を
`ci/github/classify.py` で確定し、文書だけの変更は `docs`、その他は通常の検証を実行する。
`ci-result` は実行すべき検証の成功を確認し、失敗・不明な比較元を成功にしない。
文書検査は `DOCS_BASE` の全差分、競合マーカー、秘密値、symlink を確認する。

```sh
sh ci/test-docs-check.sh
bash ci/github/test.sh
```

GitLab CI include、旧 registry 公開・取得・証跡スクリプトは削除した。
GitHub の release.py は GHCR / GitHub Releases のみを受け付ける。
GitLab URL を拒否する負例は残し、他の registry を黙って採用しない。
旧動作と結果は、この変更前の Git commit から取得できる。
