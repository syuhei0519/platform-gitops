#!/usr/bin/env sh
# Order 用 Application の描画結果と固定見本を比較し、無効化と不正入力の拒否を確認します。
# Synthetic render/schema tests only. Never registers Applications in a cluster.
set -eu
mkdir -p .rendered
fixture=ci/fixtures/order-p0
helm template workloads charts/workloads > .rendered/workloads-default.yaml
helm template workloads charts/workloads -f environments/local/workloads.yaml > .rendered/workloads.yaml
for rendered in .rendered/workloads-default.yaml .rendered/workloads.yaml; do
  cmp "$fixture/workloads-before.yaml" "$rendered"
  test "$(grep -c '^kind: Application$' "$rendered")" -eq 3
  test "$(grep -c 'targetRevision: main' "$rendered")" -eq 3
  ! grep -q 'order' "$rendered"
done
helm template workloads charts/workloads -f "$fixture/enabled.yaml" --set orderEnabled=false > .rendered/workloads-explicit-disabled.yaml
cmp "$fixture/workloads-before.yaml" .rendered/workloads-explicit-disabled.yaml
helm lint --strict charts/workloads -f environments/local/workloads.yaml -f "$fixture/enabled.yaml"
helm template workloads charts/workloads -f environments/local/workloads.yaml -f "$fixture/enabled.yaml" > .rendered/workloads-order-fixture.yaml
# Existing three Application documents are also byte-identical with Order enabled.
head -c "$(wc -c < "$fixture/workloads-before.yaml")" .rendered/workloads-order-fixture.yaml > .rendered/workloads-existing-prefix.yaml
cmp "$fixture/workloads-before.yaml" .rendered/workloads-existing-prefix.yaml
test "$(grep -c '^kind: Application$' .rendered/workloads-order-fixture.yaml)" -eq 5
test "$(grep -c 'targetRevision: main' .rendered/workloads-order-fixture.yaml)" -eq 3
test "$(grep -c 'targetRevision: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' .rendered/workloads-order-fixture.yaml)" -eq 2
test "$(grep -c 'project: lab-account' .rendered/workloads-order-fixture.yaml)" -eq 5
test "$(grep -c 'destination: {server: https://kubernetes.default.svc, namespace: account}' .rendered/workloads-order-fixture.yaml)" -eq 5
for name in order-postgresql order; do
  grep -q "name: lab-local-$name$" .rendered/workloads-order-fixture.yaml
  grep -q "path: charts/$name$" .rendered/workloads-order-fixture.yaml
  grep -Fq "valueFiles: [../../environments/local/$name.yaml]" .rendered/workloads-order-fixture.yaml
done
! grep -Eq '^kind: (Namespace|AppProject|Secret|ClusterRole|ClusterRoleBinding)$' .rendered/workloads-order-fixture.yaml
reject() {
  if helm template workloads charts/workloads "$@" > .rendered/order-rejected.yaml 2> .rendered/order-rejected.txt; then
    echo 'Unexpectedly accepted invalid Order values' >&2
    exit 1
  fi
}
for revision in '' main HEAD v1.0 aaaaaaaa AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaag ' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; do
  reject --set orderEnabled=true --set-string "orderTargetRevision=$revision"
done
for value in '"true"' null 1 '{}' '[]'; do reject --set-json "orderEnabled=$value"; done
for value in null 1 true '{}' '[]'; do reject --set orderEnabled=true --set-json "orderTargetRevision=$value"; done
reject --set orderEnabled=false --set-string orderTargetRevision=main
reject --set orderEnabled=true --set-string orderTargetRevison=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
# Defence in depth when schema checks are explicitly disabled.
reject --skip-schema-validation --set orderEnabled=true
reject --skip-schema-validation --set orderEnabled=true --set-string orderTargetRevision=main
rm -f .rendered/order-rejected.yaml .rendered/order-rejected.txt
echo 'Order disabled snapshots, synthetic enabled topology and schema rejection fixtures passed.'
