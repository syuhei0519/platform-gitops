#!/usr/bin/env sh
set -eu
kind=$1
shift
awk -v wanted="$kind" 'BEGIN { RS="---"; ORS="---\n" } $0 ~ "(^|\\n)kind: " wanted "(\\n|$)" { print $0 }' "$@"
