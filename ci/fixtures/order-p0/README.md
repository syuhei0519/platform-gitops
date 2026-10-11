Order P0 render fixtures (synthetic sample)

`workloads-before.yaml` is the unmodified raw output of Helm 4.2.4 rendering
`charts/workloads` at platform-gitops commit
`b989e29b8addbd6396c5c2b4833b969bc15fc8fa`, release name `workloads`.
Default and local values produce the same output. This fixture checks existing
Account Applications byte-for-byte and semantically. Namespace/AppProject source
files remain outside the P0 diff.

`enabled.yaml` uses a fabricated 40-hex revision solely to render the two planned
Applications. Never use it as adopted Git, image, deployment or acceptance evidence.
The revision does not claim that Order charts exist in Manifest main.
