# Existing-kind execution approval template

This tracked file is a public approval schema, not an approval decision and not
runtime evidence. Exact cluster, namespace, object, volume, credential, host,
and timing identities must be supplied in a Git-ignored runtime record.

An execution is allowed only when that runtime record:

- identifies one approved context and cluster identity;
- fixes the reviewed source revisions and immutable artifacts;
- lists every protected namespace, PVC, PV, Secret reference, and controller;
- records resource and time budgets plus stop conditions;
- records the approving person, scope, issue time, and expiry; and
- defines rollback, evidence retention, and cleanup without touching unrelated
  environments.

Placeholders or this template alone never authorize mutation. Missing,
expired, inconsistent, or non-local runtime evidence fails closed.
