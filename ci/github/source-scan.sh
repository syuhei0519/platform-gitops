#!/usr/bin/env bash
set -euo pipefail
# Checkout credentials are not persisted. Only the work tree is mounted.
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD:/work" -w /work -e SECURITY_RENDER_REQUIRED="${SECURITY_RENDER_REQUIRED:-false}" \
  --entrypoint sh aquasec/trivy:0.75.0@sha256:9db099105405c648166e6b94155eb32f8da12673cf1f455207f7385cc9a77283 -ec 'git config --global --add safe.directory /work; sh ci/source-scan.sh'
