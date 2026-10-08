#!/usr/bin/env sh
# CI/運用検証の入口。.gitlab-ci.ymlが指定する固定入力と検証器を使用する。
# 非成功終了は後続の検査を止める。成功してもcluster変更・merge権限を自動付与するものではない。
set -eu
umask 077
mkdir -p .security/private .security/public .security/source .cache/trivy
trap 'rm -rf .security/private .security/source' EXIT HUP INT TERM
# tracked sourceだけをarchive化し、Git認証設定・Runner生成file・報告/cacheをsource-scan入力へ混入させない。
git archive --format=tar HEAD > .security/private/source.tar
tar -xf .security/private/source.tar -C .security/source
scanner_exit=0
: > .security/private/empty-ignore
attempt=0
while :; do
  attempt=$((attempt + 1))
  if trivy image --download-db-only --db-repository ghcr.io/aquasecurity/trivy-db:2 --db-repository public.ecr.aws/aquasecurity/trivy-db:2 --cache-dir .cache/trivy --timeout 5m --quiet > .security/private/db.log 2>&1; then break; fi
  if [ "$attempt" -ge 2 ]; then
    printf '%s\n' '{"pass":false,"reason":"scanner DB retrieval unavailable"}' > .security/public/failure.json
    echo 'Security DB retrieval failed; no delivery permitted' >&2
    exit 2
  fi
  sleep 2
done
db_input=.cache/trivy/db/metadata.json
policy_input=security/scan-policy.json
scan_timeout=5m
# 受入用の注入は固定本番gateを厳しくする/失敗させる方向だけ。共有cacheと本番policyは変更しない。
case "${AT11_CASE:-}" in
  '') ;;
  scanner-timeout) scan_timeout=1ns ;;
  stale-db)
    printf '%s\n' '{"Version":2,"UpdatedAt":"2000-01-01T00:00:00Z"}' > .security/private/stale-db.json
    db_input=.security/private/stale-db.json ;;
  expired-exception)
    printf '%s\n' '{"schemaVersion":1,"trivyVersion":"0.75.0","dbMaxAgeHours":24,"exceptions":[{"id":"expired-acceptance","kind":"secret","rule":"fixture-rule","package":"","path":"ci/fixtures/unused.txt","owner":"Lab operator","reason":"AT11 negative acceptance only","createdAt":"2000-01-01T00:00:00Z","expiresAt":"2000-01-02T00:00:00Z"}]}' > .security/private/expired-policy.json
    policy_input=.security/private/expired-policy.json ;;
  *) echo 'Unknown acceptance case; no delivery permitted' >&2; exit 2 ;;
esac
if ! trivy fs --cache-dir .cache/trivy --skip-db-update --timeout "$scan_timeout" --quiet --no-progress --include-dev-deps --scanners vuln,secret,misconfig --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL --ignorefile .security/private/empty-ignore --list-all-pkgs --format json --output .security/private/raw.json .security/source > .security/private/scan.log 2>&1; then
  printf '%s\n' '{"pass":false,"reason":"scanner execution unavailable"}' > .security/public/failure.json
  echo 'Security scanner failed; no delivery permitted' >&2
  exit 2
fi
set -- -policy "$policy_input" -db "$db_input" -report .security/private/raw.json -out .security/public/source-scan.json -scanner-exit "$scanner_exit"
if [ "${SECURITY_RENDER_REQUIRED:-false}" = true ]; then
  test -d .rendered || { echo 'Rendered inputs missing; no delivery permitted' >&2; exit 2; }
  # tracked sourceに加え、実際のrender artifactも検査する。
  if ! trivy fs --cache-dir .cache/trivy --skip-db-update --timeout 5m --quiet --no-progress --scanners vuln,secret,misconfig --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL --ignorefile .security/private/empty-ignore --format json --output .security/private/render-raw.json .rendered > .security/private/render.log 2>&1; then
    echo 'Rendered scanner failed; no delivery permitted' >&2;exit 2
  fi
  .security/scan-gate -policy "$policy_input" -db "$db_input" -report .security/private/render-raw.json -out .security/public/render-scan.json -scanner-exit 0 -rendered .rendered
fi
if ! .security/scan-gate "$@"; then
  printf '%s\n' '{"pass":false,"reason":"security policy gate rejected scanner inputs or findings"}' > .security/public/failure.json
  exit 1
fi
