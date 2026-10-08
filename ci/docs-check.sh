#!/bin/sh
# 文書専用CIから呼ぶ。比較差分の許可パスを再確認し、競合マーカーとsecretを検査する。
# 入力はcheckoutとGitLabの比較元情報。通常CI・依存更新・イメージ公開・配備は行わない。
# 失敗は非0で停止。検出内容を含み得るTrivy出力は非公開一時領域で扱い、終了時に削除する。
set -eu
umask 077
fail() { echo 'Documentation check failed; inspect the changed Markdown or CI configuration locally.' >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 1' HUP INT TERM
mkdir "$work/input"
git rev-parse --verify HEAD > /dev/null 2>&1 || fail
case "${CI_PIPELINE_SOURCE:-}" in
  merge_request_event) base=${CI_MERGE_REQUEST_DIFF_BASE_SHA:-} ;;
  push)
    if [ "${CI_COMMIT_BRANCH:-}" = "${CI_DEFAULT_BRANCH:-}" ]; then
      base=${CI_COMMIT_BEFORE_SHA:-}
    else
      # includeのcompare_toと同じmainを読む。pipeline作成後にmainが進んだ場合も未知パスは失敗させる。
      [ -n "${CI_DEFAULT_BRANCH:-}" ] || fail
      git fetch --no-tags --depth=1 origin "refs/heads/$CI_DEFAULT_BRANCH" > "$work/fetch.log" 2>&1 || fail
      base=$(git rev-parse FETCH_HEAD) || fail
    fi ;;
  *) fail ;;
esac
# 値をGitのoptionや任意refとして扱わない。MR/pushの比較元は40桁commit SHAだけ許可する。
[ "${#base}" -eq 40 ] || fail
case "$base" in *[!0-9a-f]*|0000000000000000000000000000000000000000) fail ;; esac
if ! git cat-file -e "$base^{commit}" 2>/dev/null; then
  git fetch --no-tags --depth=1 origin "$base" > "$work/fetch.log" 2>&1 || fail
fi
# renameは削除+追加として扱い、文書→コードへの移動を許可パス判定から漏らさない。
# Gitは改行/制御文字を含む名前をquoteする。その名前は以下の許可条件で失敗させる。
git -c core.quotepath=false diff --name-only --no-renames "$base" HEAD > "$work/paths" || fail
count=0
while IFS= read -r file; do
  case "$file" in README.md|CHANGELOG.md|CONTRIBUTING.md|docs/*.md) ;; *) fail ;; esac
  # 削除された文書は現checkoutにないので検査入力から除く。symlinkは外部ファイル参照を防ぐため拒否。
  [ ! -L "$file" ] || fail
  [ -e "$file" ] || continue
  [ -f "$file" ] || fail
  # コード例のfence内は対象外。CommonMarkで有効な未閉鎖fenceを独自の構文エラーにはしない。
  if ! awk '
    BEGIN { fence = ""; width = 0; bad = 0 }
    {
      line = $0; sub(/\r$/, "", line)
      for (indent = 0; indent < 3 && substr(line, 1, 1) == " "; indent++) line = substr(line, 2)
      if (line ~ /^```/ || line ~ /^~~~/) {
        mark = substr(line, 1, 1); n = 0
        while (substr(line, n + 1, 1) == mark) n++
        rest = substr(line, n + 1)
        if (fence == "" && (mark != "`" || rest !~ /`/)) { fence = mark; width = n; next }
        if (fence == mark && n >= width && rest ~ /^[ \t]*$/) { fence = ""; width = 0; next }
      }
      if (fence == "" && line ~ /^(<<<<<<<|=======|>>>>>>>)( |$)/) bad = 1
    }
    END { exit bad }
  ' "$file"; then fail; fi
  mkdir -p "$work/input/$(dirname "$file")"
  cp "$file" "$work/input/$file"
  count=$((count + 1))
done < "$work/paths"
if [ "$count" -gt 0 ]; then
  : > "$work/empty-ignore"
  printf '{}\n' > "$work/config.yaml"
  # Trivyは既定でMarkdownをallowするため、この専用設定で除外を解除する。
  # checkout内のtrivy.yaml/trivy-secret.yamlを自動読込させず、検査対象自身による除外を防ぐ。
  printf 'disable-allow-rules:\n  - markdown\n' > "$work/secret-config.yaml"
  # --exit-code 1はsecret検出を失敗にする。scanner異常も非0なので成功として扱わない。
  # 2分は軽量jobの5分上限内でclone/検査/後始末の時間を残す実行予算で、性能保証ではない。
  if ! trivy fs --scanners secret --exit-code 1 --timeout 2m --quiet --no-progress \
    --config "$work/config.yaml" --secret-config "$work/secret-config.yaml" \
    --ignorefile "$work/empty-ignore" --cache-dir "$work/cache" \
    --format json --output "$work/report.json" "$work/input" > "$work/scan.log" 2>&1; then fail; fi
fi
printf 'Documentation checks passed (%s current Markdown files; deletions excluded).\n' "$count"
