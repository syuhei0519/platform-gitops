# Istio 1.31.2 runtime follow-up (approval pending)

The approved bootstrap stopped on Forbidden; no extra runtime authority has been applied.
The standard `ISTIO_WATCH_NAMESPACE=learning-account-release` setting scopes namespace-empty
namespaced informers, including the local credentials Secret informer. It leaves XDS
identity checks enabled and uses the existing pinned Istio image. No fork/new controller.

Proposed additional Role boundaries for SA `istio-learning-system/istiod-learning-1-31-2`:

* `learning-account-release/learning-istiod-credentials`: core secrets **list/watch only**.
  Kubernetes list/watch returns full values. The new Account DB Secret is therefore readable;
  this is effective credential access, not public certificate metadata. No get/write/delete
  or permission in old services/Sonar namespaces. RBAC cannot enforce the informer's type
  exclusions (Helm release and service-account-token Secrets); those are optimization only.
* `istio-learning-system/learning-istiod-runtime`: core configmaps **update only**, restricted
  to `istio-leader` and `istio-ip-autoallocate`. Existing get/create already exist. These are
  leader-election records, not CA/private values. No unrelated CM update or delete.

Cluster Secret read is prohibited. Public CA ConfigMap distribution and existing control-
namespace CA Secret access are unchanged. The CA fingerprint must still match before leaf
issuance. The two roles above remain an explicit operator security-bootstrap exception;
normal workload deployment remains Git/PR/Argo. Review actual rendered role names and rules
before applying. Stop on any further Forbidden; do not widen access automatically.

Sources, pinned to upstream 1.31.2:

* [feature setting](https://github.com/istio/istio/blob/1.31.2/pilot/pkg/features/pilot.go)
* [ToOpts namespace default](https://github.com/istio/istio/blob/1.31.2/pkg/kube/kclient/client.go)
* [local credentials informer](https://github.com/istio/istio/blob/1.31.2/pilot/pkg/credentials/kube/secrets.go)
* [local vs remote controller](https://github.com/istio/istio/blob/1.31.2/pilot/pkg/credentials/kube/multicluster.go)
* [explicit control namespace multicluster watch](https://github.com/istio/istio/blob/1.31.2/pkg/kube/multicluster/secretcontroller.go)

The Account sidecar does not intrinsically require reading every namespace's Secret values.
The default local credentials cache does so because it supports gateway TLS / generic
credential and Wasm pull use cases. Discovery selectors/ObjectFilter filter objects after
API authorization; they are not a per-namespace API boundary. The standard single-namespace
setting avoids that cluster permission for this Account-only mesh. A multi-namespace mesh
would require a separate reviewed design. `LOCAL_CLUSTER_SECRET_WATCHER` selects a different
multicluster registry client and does not scope this local credentials informer. Disabling
XDS identity checks is not an accepted alternative. A custom fork or extra controller would
add maintenance/build cost and is unnecessary for the supported single-namespace option.

After explicit approval, apply only these added role/binding identities, then sync the
Istiod Deployment from reviewed main. Require readiness, no Forbidden, CA identity unchanged,
correct admission bundles and Account injection before enabling subsequent traffic. This
source review/render is not a runtime pass; Account acceptance and PLAT-199 remain pending.

## Public CA namespace-controller lock follow-up (additional approval pending)

After the two reviewed lock updates were permitted, the standard CA distribution
controller attempted to update `istio-learning-system/istio-namespace-controller-election`.
The API denied that exact action. Istiod is Ready, but Account traffic remains stopped.
Add only this third resourceName to the existing `learning-istiod-runtime` Role's
ConfigMap update rule. No extra verb, namespace, Secret authority or workload change.
This lock elects the controller distributing the existing public root certificate
ConfigMap to the selected namespaces; it is not a CA private key or root rotation.
The CA Secret UID and stored public fingerprint were rechecked unchanged. An initial
fingerprint assertion failure was a copied-string omission in the operator checker;
comparison now reads the captured public-metadata file, and passes.

The fixed 1.31.2 source defines all lock names in
[pilot/pkg/leaderelection/leaderelection.go](https://github.com/istio/istio/blob/1.31.2/pilot/pkg/leaderelection/leaderelection.go).
The active namespace controller is started by
[multicluster.go](https://github.com/istio/istio/blob/1.31.2/pilot/pkg/serviceregistry/kube/controller/multicluster.go#L299).
Other gateway/status/analysis/MCS/node-untaint paths remain disabled in this configuration;
no permissions for their lock names are requested. Stop on any further Forbidden.
The additional update permission is not applied until separately approved.
