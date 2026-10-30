# Redis 8.6.4 Alpine preparation

Redis 8 permits selection of AGPLv3 from its tri-license. The complete tagged
license, exact source archive hash, and official-image Dockerfile (including
its protected-mode source edit) are retained or linked here as corresponding
source material. The local Dockerfile has one normalized final newline; its
upstream Git blob identity is recorded separately.

The fixed linux/amd64 OCI manifest/config, current vulnerability gate, and
Alpine runtime package license/source mapping are recorded in `evidence.json`.
The `.redis-rundeps` entry is an apk virtual dependency group created by the
retained Dockerfile; no license is inferred for it. Redis itself is provided
through the separately retained tagged source and AGPL license rather than the
OS-package portion of the SBOM.
