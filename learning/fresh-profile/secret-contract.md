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

G3-P must name the exact target, Secret type/key names, owner, expiry, issuer,
issuance procedure reference/hash and cleanup owner before manual creation.
The current profile validator validates and seals the hash string; it does not
read the referenced procedure bytes, so content review must remain an explicit
approval-side responsibility.
Fresh-isolated credentials and a fresh-sandbox CA are the default. G3-P is
authority and intent, not proof that anything was issued. G3-R is accepted only
at `credential-bootstrap` from non-secret issuance receipt hashes and exact
fresh Secret/CA metadata plus UIDs, and it must be journaled before any later
step. Reading or moving an existing credential or CA is a separate approval.
Evidence records metadata and key names only.

The checked live adapter currently has no safe metadata-only Secret/CA receipt
transport. Until that narrow path is separately implemented and reviewed, a
full-plan invocation is rejected before executor identity, reconciliation or
mutation; a generic Deployment or ordinary Secret JSON response does not
satisfy this contract.

The application source at `4a98e85a3866e48868c0c69505e546ac73f67641`
removes the `uri` fallback and consumes only `username` and `password` for the
runtime and migration operations. The local Admission allowlist now matches
those exact operation-scoped keys: the migration Job cannot read
bootstrap/runtime keys, the runtime Rollout cannot read migration/bootstrap
keys, and `uri` is not an accepted fallback. This is code-only evidence;
applying the policy and proving effective denial remain separately approved
live work.
