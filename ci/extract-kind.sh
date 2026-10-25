#!/usr/bin/env sh
# 描画済み YAML から指定した kind の文書だけを取り出し、CI のリソース別検査へ渡します。
set -eu
kind=$1
shift
awk -v wanted="$kind" 'BEGIN { RS="---"; ORS="---\n" } $0 ~ "(^|\\n)kind: " wanted "(\\n|$)" { print $0 }' "$@"
