#!/bin/sh
# 実Git差分とfake scannerで、文書専用jobの失敗閉鎖・削除/rename・情報非表示・後始末を検証する。
# 実tokenや脆弱性DB、リモート配備は使わない。通常CIとdocs-checkの両方から実行する。
set -eu
checker="$PWD/ci/docs-check.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 1' HUP INT TERM
mkdir -p "$work/repo" "$work/bin" "$work/tmp"
cat > "$work/bin/trivy" <<'SH'
#!/bin/sh
set -eu
# ログに見つかった内容を出すscannerを模擬し、親scriptが外へ漏らさないことを確認する。
printf 'private-test-marker\n'
case "${SCAN_RESULT:-pass}" in finding) exit 1 ;; error) exit 2 ;; esac
config=''
previous=''
for arg in "$@"; do
  if [ "$previous" = --secret-config ]; then config=$arg; fi
  previous=$arg
done
test -f "$config"
grep -q '  - markdown' "$config"
while [ "$#" -gt 1 ]; do shift; done
test -f "$1/README.md" || test -f "$1/docs/new name.md"
SH
chmod +x "$work/bin/trivy"
export PATH="$work/bin:$PATH" TMPDIR="$work/tmp"
cd "$work/repo"
git init -q
git config user.email fixture@example.invalid
git config user.name 'CI fixture'
git config commit.gpgsign false
git config core.autocrlf false
printf '# Before\n' > README.md
git add README.md
git commit -qm before
base=$(git rev-parse HEAD)
export CI_PIPELINE_SOURCE=merge_request_event CI_MERGE_REQUEST_DIFF_BASE_SHA="$base"
run_case() {
  expected=$1
  status=0
  sh "$checker" > "$work/result" 2>&1 || status=$?
  if [ "$expected" = pass ]; then test "$status" -eq 0; else test "$status" -ne 0; fi
  if grep -q private-test-marker "$work/result"; then echo 'Scanner output leaked' >&2; exit 1; fi
  test -z "$(ls -A "$work/tmp")"
}
printf '# After\n```text\n<<<<<<< example\n```\n' > README.md
git commit -qam docs
run_case pass
export SCAN_RESULT=finding
run_case fail
export SCAN_RESULT=error
run_case fail
export SCAN_RESULT=pass
mkdir docs
git mv README.md 'docs/new name.md'
git commit -qm rename
run_case pass
git rm -q 'docs/new name.md'
git commit -qm delete
run_case pass
printf '# Conflict\n<<<<<<< branch\n' > README.md
git add README.md
git commit -qm conflict
run_case fail
git reset -q --hard "$base"
printf '# After\n' > README.md
printf 'code\n' > app.go
git add README.md app.go
git commit -qm mixed
run_case fail
git reset -q --hard "$base"
git mv README.md app.go
git commit -qm non-doc-rename
run_case fail
git reset -q --hard "$base"
rm README.md
ln -s /etc/passwd README.md
git add README.md
git commit -qm symlink
run_case fail
git reset -q --hard "$base"
export CI_PIPELINE_SOURCE=api
run_case fail
export CI_PIPELINE_SOURCE=push CI_COMMIT_BRANCH=main CI_DEFAULT_BRANCH=main CI_COMMIT_BEFORE_SHA="$base"
printf '# Main push\n' > README.md
git commit -qam push
run_case pass
export CI_COMMIT_BEFORE_SHA=0000000000000000000000000000000000000000
run_case fail
# branch pushは直前commitではなくdefault branchとの差分全体。mainに追随するfetchも実Gitで確認する。
export CI_COMMIT_BRANCH=docs CI_COMMIT_BEFORE_SHA="$base"
git update-ref refs/heads/main "$base"
git remote add origin .
run_case pass
printf 'code\n' > app.go
git add app.go
git commit -qm code
printf '# Docs after code\n' > README.md
git commit -qam docs-after-code
run_case fail
echo 'Documentation checker regression tests passed (14 cases).'
