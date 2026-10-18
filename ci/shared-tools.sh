#!/usr/bin/env bash
# Materialize immutable shared Go modules; generated source is never committed here.
set -euo pipefail
cd "$(dirname "$0")/.."
revision=6e354326159c01984e4c857895a31d052ed7f91e
shared=$(mktemp -d)
trap 'rm -rf "$shared"' EXIT
# Fetch exactly the reviewed source commit. A fetch failure must fail CI.
git -C "$shared" init -q
git -C "$shared" remote add origin https://github.com/syuhei0519/backend-app.git
git -C "$shared" fetch --quiet --depth=1 origin "$revision"
test "$(git -C "$shared" rev-parse FETCH_HEAD)" = "$revision"
# Refuse pre-existing source to avoid silently using an unpinned local implementation.
for tool in scan-gate; do
  test ! -e "tools/$tool" || { echo "Remove generated tools/$tool before fetching" >&2; exit 1; }
done
git -C "$shared" archive "$revision" tools/scan-gate | tar -x -C "$PWD"
