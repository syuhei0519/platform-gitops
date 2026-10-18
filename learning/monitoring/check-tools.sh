#!/bin/sh
# Run after validate.py --output <directory>; Docker only, no Kubernetes calls.
set -eu
config_dir=$(cd "${1:?synthetic configuration directory required}" && pwd)
fixture_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
prometheus=quay.io/prometheus/prometheus:v3.15.0@sha256:86b17a25c2db1d16a61b16b3c8f336679eb19e26333d03e808da387206e40faa
collector=ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector-contrib:0.161.0@sha256:b5cf983651c32c3ca13f936deb51742015a54d121f388cac248923ddeb8cc9fc
docker run --rm --network none --user "$(id -u):$(id -g)" --entrypoint /bin/promtool \
  -v "$config_dir:/config:ro" "$prometheus" check config /config/prometheus.yml
docker run --rm --network none --user "$(id -u):$(id -g)" --entrypoint /bin/promtool \
  -v "$fixture_dir:/work:ro" -w /work "$prometheus" test rules query-tests.yaml
docker run --rm --network none --user "$(id -u):$(id -g)" --entrypoint /bin/promtool \
  -v "$config_dir:/work:ro" -w /work "$prometheus" test rules sli-query-tests.yaml
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -v "$config_dir:/config:ro" "$collector" validate --config=/config/config.yaml
