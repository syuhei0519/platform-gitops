#!/usr/bin/sh
# Candidate for an Argo exec provider; never invoke against a real Pod manually.
# stdout is the Kubernetes credential protocol, consumed only by client-go.
set +x
set -eu
token_file=/var/run/secrets/kubernetes.io/serviceaccount/token
test -r "$token_file" || exit 1
token=''
IFS= read -r token < "$token_file" || test -n "$token"
case "$token" in
  ''|*[!A-Za-z0-9._-]*) exit 1 ;;
esac
expires=$(date -u -d '+1 minute' '+%Y-%m-%dT%H:%M:%SZ')
printf '{"apiVersion":"client.authentication.k8s.io/v1beta1","kind":"ExecCredential","status":{"token":"%s","expirationTimestamp":"%s"}}\n' "$token" "$expires"
