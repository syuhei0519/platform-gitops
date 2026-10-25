# Fresh credential references

This repository contains references only. No Secret, token, password, private
key, CA key, or credential value may be committed or printed.

The `fresh-account` namespace uses exactly:

- `Secret/account-db-bootstrap`, key `admin-password`, consumed only by
  `StatefulSet/account-postgresql` and `Job/account-db-role-bootstrap`.
- `Secret/account-db-migration`, keys `username` and `password`,
  consumed only by `Job/account-db-role-bootstrap` and
  `Job/account-migration`.
- `Secret/account-db-runtime`, keys `username` and `password`, consumed
  only by `Job/account-db-role-bootstrap` and `Rollout/account-api`.
- `Secret/fresh-control/repository-read` only if public Git read fails.

G3 must name the owner, purpose, expiry, recovery source, rotation deadline,
and cleanup owner before manual creation. Fresh-isolated credentials and a
fresh-sandbox CA are the default. Reading or moving an existing credential or
CA is a separate approval. Evidence records metadata and key names only.

The application source at `331dc4ba0eabb531fe816eaffcca4cbf3f41faa4`
removes the `uri` fallback and consumes only `username` and `password` for the
runtime and migration operations. The local Admission allowlist now matches
those exact operation-scoped keys: the migration Job cannot read
bootstrap/runtime keys, the runtime Rollout cannot read migration/bootstrap
keys, and `uri` is not an accepted fallback. This is code-only evidence;
applying the policy and proving effective denial remain separately approved
live work.
