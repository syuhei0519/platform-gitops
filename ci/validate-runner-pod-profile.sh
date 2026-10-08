#!/usr/bin/env sh
# CI/運用検証の入口。.gitlab-ci.ymlが指定する固定入力と検証器を使用する。
# 非成功終了は後続の検査を止める。成功してもcluster変更・merge権限を自動付与するものではない。
set -eu
rendered=${1:-.rendered/gitlab-runner.yaml}

for expected in \
  'namespace = "build"' 'service_account = "ci-job"' 'privileged = false' \
  'limit = 1' 'request_concurrency = 1' 'maximum_timeout = 1200' \
  'poll_timeout = 180' 'cleanup_resources_timeout = "5m"' \
  'cpu_request = "500m"' 'cpu_limit = "2"' \
  'memory_request = "512Mi"' 'memory_limit = "2Gi"' \
  'ephemeral_storage_request = "2Gi"' 'ephemeral_storage_limit = "14Gi"' \
  'helper_cpu_request = "100m"' 'helper_cpu_limit = "500m"' \
  'helper_memory_request = "128Mi"' 'helper_memory_limit = "256Mi"' \
  'helper_image = "registry.gitlab.com/gitlab-org/ci-cd/gitlab-runner-ubi-images/gitlab-runner-helper-ocp:x86_64-v19.3.1@sha256:ebcce5b4e36899e72aa20554962d7270a0eec1e398e1b0a0f746ede4f4bb1b70"' \
  'run_as_user = 1000' 'run_as_group = 1000' \
  'run_as_group = 0' \
  'allow_privilege_escalation = true' 'type = "Unconfined"' \
  'name = "repo"' 'size_limit = "2Gi"' \
  'name = "buildkit-state"' 'size_limit = "10Gi"'; do
  grep -Fq "$expected" "$rendered"
done

test "$(grep -c '^kind: Deployment$' "$rendered")" -eq 1
test "$(grep -c '^kind: ConfigMap$' "$rendered")" -eq 1
! grep -Eq '^kind: (Secret|Role|RoleBinding|ClusterRole|ClusterRoleBinding|ServiceAccount)$' "$rendered"
grep -A5 'name: CI_SERVER_TOKEN' "$rendered" | grep -q 'name: gitlab-runner-auth'
grep -A5 'name: CI_SERVER_TOKEN' "$rendered" | grep -q 'key: runner-token'
! grep -q 'runnerRegistrationToken:' "$rendered"
! grep -q 'privileged = true' "$rendered"
! grep -q 'SYS_ADMIN' "$rendered"
! grep -q 'hostPath:' "$rendered"
! grep -q 'docker.sock' "$rendered"
