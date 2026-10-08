#!/usr/bin/env sh
# CI/運用検証の入口。.gitlab-ci.ymlが指定する固定入力と検証器を使用する。
# 非成功終了は後続の検査を止める。成功してもcluster変更・merge権限を自動付与するものではない。
set -eu
mkdir -p .rendered

helm repo add gitlab https://charts.gitlab.io --force-update
for chart in root platform workloads runner-foundation observability-foundation otel-collector grafana; do
  helm lint --strict "charts/$chart" -f "environments/local/$chart.yaml"
done
helm template root charts/root -f environments/local/root.yaml --set workloadsEnabled=false > .rendered/root-disabled.yaml
helm template root charts/root -f environments/local/root.yaml --set workloadsEnabled=true > .rendered/root-enabled.yaml
helm template platform charts/platform -f environments/local/platform.yaml --set observabilityEnabled=false --set prometheusEnabled=false --set collectorEnabled=false --set grafanaEnabled=false > .rendered/platform.yaml
helm template platform charts/platform -f environments/local/platform.yaml > .rendered/platform-observability.yaml
helm template workloads charts/workloads -f environments/local/workloads.yaml > .rendered/workloads.yaml
helm template runner-foundation charts/runner-foundation -f environments/local/runner-foundation.yaml > .rendered/runner-foundation.yaml
helm template observability-foundation charts/observability-foundation --namespace observability -f environments/local/observability-foundation.yaml > .rendered/observability-foundation.yaml
helm template otel-collector charts/otel-collector --namespace observability -f environments/local/otel-collector.yaml --set newRelicEnabled=false > .rendered/collector-disabled.yaml
helm template otel-collector charts/otel-collector --namespace observability -f environments/local/otel-collector.yaml --set newRelicEnabled=true > .rendered/collector-enabled.yaml
helm template otel-collector charts/otel-collector --namespace observability -f environments/local/otel-collector.yaml --set newRelicEnabled=true --show-only templates/configmap.yaml | sed -n '/^  config.yaml: |$/,$p' | tail -n +2 | sed 's/^    //' > .rendered/collector-config.yaml
echo '5189a48131311513ef7bf5e22f1ed6424cd1c7cec61bc1995fab04d3cf46f36a  charts/grafana/charts/grafana-13.2.7.tgz' | sha256sum -c -
helm template grafana charts/grafana --namespace observability -f environments/local/grafana.yaml > .rendered/grafana.yaml
! grep -Eq '^kind: (Secret|ClusterRole|ClusterRoleBinding)$' .rendered/grafana.yaml
for dashboard in observability/dashboards/*.json; do cmp "$dashboard" "charts/grafana/files/$(basename "$dashboard")"; done
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo add argo https://argoproj.github.io/argo-helm --force-update
mkdir -p .rendered/.charts
helm pull prometheus-community/prometheus --version 29.35.0 --destination .rendered/.charts
echo '58bd043404026c30298d2f319711fd32c91e255f1c9d29be557a3d465ad91239  .rendered/.charts/prometheus-29.35.0.tgz' | sha256sum -c -
helm lint --strict .rendered/.charts/prometheus-29.35.0.tgz -f environments/local/prometheus.yaml
helm template prometheus .rendered/.charts/prometheus-29.35.0.tgz --namespace observability -f environments/local/prometheus.yaml > .rendered/prometheus.yaml
helm pull argo/argo-cd --version 10.8.2 --destination .rendered/.charts
echo '96fb5ab2897e3c8758cd8fef409a46668ef1a19a538116b112b0691614a48f20  .rendered/.charts/argo-cd-10.8.2.tgz' | sha256sum -c -
# Podを生成するtemplateだけを選ぶ。render済みSecret本文は出力しない。
helm template argocd .rendered/.charts/argo-cd-10.8.2.tgz --namespace argocd -f bootstrap/argocd-values.yaml \
  --show-only templates/argocd-application-controller/statefulset.yaml \
  --show-only templates/argocd-repo-server/deployment.yaml \
  --show-only templates/argocd-server/deployment.yaml \
  --show-only templates/argocd-applicationset/deployment.yaml \
  --show-only templates/redis/deployment.yaml \
  --show-only templates/redis-secret-init/job.yaml > .rendered/argocd-pods.yaml
helm template gitlab-runner gitlab/gitlab-runner --version 0.92.1 --namespace runner-system -f environments/local/gitlab-runner.yaml > .rendered/gitlab-runner.yaml
! grep -Eq '^kind: Secret$' .rendered/argocd-pods.yaml .rendered/prometheus.yaml .rendered/gitlab-runner.yaml

test "$(grep -c '^kind: Application$' .rendered/root-disabled.yaml)" -eq 1
test "$(grep -c '^kind: Application$' .rendered/root-enabled.yaml)" -eq 2
test "$(grep -c '^kind: Application$' .rendered/platform.yaml)" -eq 2
test "$(grep -c '^kind: Application$' .rendered/platform-observability.yaml)" -eq 6
test "$(grep -c '^kind: Application$' .rendered/workloads.yaml)" -eq 3
test "$(cat .rendered/root-enabled.yaml .rendered/platform.yaml .rendered/workloads.yaml | grep -c '^kind: Application$')" -eq 7
! grep -R -q 'resources-finalizer.argocd.argoproj.io' .rendered
! grep -R -Eq '^kind: (Namespace|Secret|ClusterRole|ClusterRoleBinding)$' .rendered/root-*.yaml .rendered/platform.yaml .rendered/workloads.yaml .rendered/runner-foundation.yaml
test "$(grep -c 'argocd.argoproj.io/sync-options: Prune=confirm' .rendered/root-enabled.yaml)" -eq 2
test "$(grep -c 'argocd.argoproj.io/sync-options: Prune=confirm' .rendered/platform.yaml)" -eq 2
test "$(grep -c 'argocd.argoproj.io/sync-options: Prune=confirm' .rendered/workloads.yaml)" -eq 3
grep -q 'targetRevision: "0.92.1"' .rendered/platform.yaml
grep -q 'valueFiles: \[\$values/environments/local/gitlab-runner.yaml\]' .rendered/platform.yaml
grep -q 'kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed' bootstrap/kind.yaml
grep -q 'version: 0.92.1' bootstrap/versions.lock.yaml
grep -q 'gitlab-runner-helper-ocp:x86_64-v19.3.1@sha256:ebcce5b4e36899e72aa20554962d7270a0eec1e398e1b0a0f746ede4f4bb1b70' bootstrap/versions.lock.yaml
grep -q 'runnerChartVersion: 0.92.1' environments/local/platform.yaml
grep -q 'moby/buildkit:v0.30.0-rootless@sha256:d76eb1caecac5733ef7553c1e90a1b21f1bb218cd1142d3553de0747b4a14ba9' bootstrap/versions.lock.yaml
sh ci/validate-runner-pod-profile.sh .rendered/gitlab-runner.yaml
sh ci/extract-kind.sh Application .rendered/root-enabled.yaml .rendered/platform-observability.yaml .rendered/workloads.yaml > .rendered/applications.yaml
sh ci/extract-kind.sh AppProject .rendered/root-enabled.yaml > .rendered/projects.yaml
