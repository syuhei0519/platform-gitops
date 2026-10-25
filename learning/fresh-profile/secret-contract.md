# Fresh credential references

This repository contains references only. No Secret, token, password, private
key, CA key, or credential value may be committed or printed.

The `fresh-account` namespace uses exactly:

- `Secret/account-db-bootstrap`, key `admin-password`, consumed only by
  `StatefulSet/account-postgresql` and `Job/account-db-role-bootstrap`.
- `Secret/account-db-migration`, keys `username`, `password`, and `uri`,
  consumed only by `Job/account-db-role-bootstrap` and
  `Job/account-migration`.
- `Secret/account-db-runtime`, keys `username`, `password`, and `uri`, consumed
  only by `Job/account-db-role-bootstrap` and `Rollout/account-api`.
- `Secret/fresh-control/repository-read` only if public Git read fails.

G3 must name the owner, purpose, expiry, recovery source, rotation deadline,
and cleanup owner before manual creation. Fresh-isolated credentials and a
fresh-sandbox CA are the default. Reading or moving an existing credential or
CA is a separate approval. Evidence records metadata and key names only.

The application source at `9e6ef64cf4e37eb52612daa7e4a35e48f3f39754`
removes the `uri` fallback and consumes only `username` and `password` for the
runtime and migration operations. The current Admission allowlist above has
not been widened or rewritten under the Helm/Kustomize override approval.
`profile.lock.yaml` therefore records
`PENDING_EXACT_ADMISSION_SECRET_KEY_APPROVAL`; no live bundle may pass the gate
until an exact local-code approval aligns this contract, the canonical
security contract, fixtures, and policy together.
