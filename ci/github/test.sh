#!/usr/bin/env bash
set -euo pipefail
mkdir -p .security .build
sh ci/test-docs-check.sh
for module in tools/*/go.mod; do
  dir=${module%/go.mod}
  if test "$dir" = tools/checksum; then continue; fi
  (cd "$dir"; go test ./...)
done
(cd tools/scan-gate; CGO_ENABLED=0 go build -trimpath -o ../../.security/scan-gate .)
(cd tools/bootstrap; CGO_ENABLED=0 go test -c -o ../../.build/bootstrap-test .)
if test -f ci/github/release_test.py; then python3 -I ci/github/release_test.py; fi
