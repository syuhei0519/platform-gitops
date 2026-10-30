#!/bin/sh
# Account のアラート定義について、生成内容と規則の検査をまとめて実行する入口です。
# Offline rule/config fixtures followed by internal-network Docker integration.
set -eu
config_dir=$(cd "${1:?render output required}" && pwd)
fixture_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
prometheus=ghcr.io/syuhei0519/platform-lab-images:prometheus-v3.15.0@sha256:86b17a25c2db1d16a61b16b3c8f336679eb19e26333d03e808da387206e40faa
alertmanager=quay.io/prometheus/alertmanager:v0.32.0@sha256:58e117eabccebbff04e6643a3432d6315a2cc3a8c24ab5849bc628886bf08857
docker run --rm --network none --user "$(id -u):$(id -g)" --entrypoint /bin/promtool -v "$config_dir:/etc/prometheus:ro" "$prometheus" check config /etc/prometheus/prometheus.yml
docker run --rm --network none --user "$(id -u):$(id -g)" --entrypoint /bin/promtool -v "$config_dir:/work:ro" -w /work "$prometheus" test rules alert-tests.yaml
docker run --rm --network none --user "$(id -u):$(id -g)" --entrypoint /bin/amtool -v "$config_dir:/config:ro" "$alertmanager" check-config /config/alertmanager.yml
python3 "$fixture_dir/integration.py" "$config_dir"
