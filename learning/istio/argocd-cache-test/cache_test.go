// Argo CD の cache 読み取りに必要な範囲と、禁止したアクセスの境界を確認します。
// Injected only into the fixed upstream GitOps Engine checkout by check_argocd_cache.py.
package cache

import (
	"encoding/json"
	"os"
	"slices"
	"testing"
	"time"

	"github.com/argoproj/argo-cd/gitops-engine/pkg/utils/kube"
	"github.com/argoproj/argo-cd/gitops-engine/pkg/utils/kube/kubetest"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic/fake"
	"k8s.io/client-go/rest"
	ktesting "k8s.io/client-go/testing"
)

func TestIstioRegistrationCache(t *testing.T) {
	const account = "learning-services-20261018"
	const demo = "learning-istio-demo"
	type rule struct {
		Groups    []string `json:"apiGroups"`
		Resources []string `json:"resources"`
		Verbs     []string `json:"verbs"`
	}
	roles := map[string][]rule{}
	data, err := os.ReadFile("roles.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(data, &roles); err != nil {
		t.Fatal(err)
	}
	resources := []struct{ group, version, kind, plural string }{
		{"argoproj.io", "v1alpha1", "Rollout", "rollouts"},
		{"argoproj.io", "v1alpha1", "AnalysisTemplate", "analysistemplates"},
		{"argoproj.io", "v1alpha1", "AnalysisRun", "analysisruns"},
		{"apps", "v1", "Deployment", "deployments"},
		{"apps", "v1", "ReplicaSet", "replicasets"},
		{"", "v1", "Pod", "pods"}, {"", "v1", "Service", "services"},
		{"networking.istio.io", "v1", "VirtualService", "virtualservices"},
		{"networking.istio.io", "v1", "DestinationRule", "destinationrules"},
	}
	for _, tc := range []struct {
		name                    string
		namespaces              []string
		mode                    int
		wantError, wantMetadata bool
	}{
		{"original-union-disabled-fails", []string{account, demo}, RespectRbacDisabled, true, false},
		{"normal-drops-shared-kind-metadata", []string{account, demo}, RespectRbacNormal, false, false},
		{"demo-only-disabled-works", []string{demo}, RespectRbacDisabled, false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			apis := []kube.APIResourceInfo{}
			lists := map[schema.GroupVersionResource]string{}
			objs := []runtime.Object{}
			for _, r := range resources {
				gvr := schema.GroupVersionResource{Group: r.group, Version: r.version, Resource: r.plural}
				lists[gvr] = r.kind + "List"
				apis = append(apis, kube.APIResourceInfo{GroupKind: schema.GroupKind{Group: r.group, Kind: r.kind}, GroupVersionResource: gvr, Meta: metav1.APIResource{Name: r.plural, Kind: r.kind, Namespaced: true}})
				objs = append(objs, &unstructured.Unstructured{Object: map[string]any{"apiVersion": gvr.GroupVersion().String(), "kind": r.kind, "metadata": map[string]any{"name": "fixture", "namespace": demo, "resourceVersion": "1"}}})
			}
			client := fake.NewSimpleDynamicClientWithCustomListKinds(runtime.NewScheme(), lists, objs...)
			client.PrependReactor("list", "*", func(a ktesting.Action) (bool, runtime.Object, error) {
				allowed := false
				for _, r := range roles[a.GetNamespace()] {
					if slices.Contains(r.Groups, a.GetResource().Group) && slices.Contains(r.Resources, a.GetResource().Resource) && slices.Contains(r.Verbs, "list") {
						allowed = true
					}
				}
				if !allowed {
					return true, nil, apierrors.NewForbidden(a.GetResource().GroupResource(), "", nil)
				}
				return false, nil, nil
			})
			cache := NewClusterCache(&rest.Config{Host: "https://fixture"}, SetKubectl(&kubetest.MockKubectlCmd{APIResources: apis, DynamicClient: client}), SetNamespaces(tc.namespaces), SetRespectRBAC(tc.mode), SetWatchResyncTimeout(time.Hour), SetPopulateResourceInfoHandler(func(*unstructured.Unstructured, bool) (any, bool) { return nil, true }))
			defer cache.Invalidate()
			err := cache.EnsureSynced()
			if (err != nil) != tc.wantError {
				t.Fatalf("EnsureSynced error=%v expected error=%v", err, tc.wantError)
			}
			if tc.wantError {
				return
			}
			for _, kind := range []string{"VirtualService", "DestinationRule"} {
				_, err := cache.IsNamespaced(schema.GroupKind{Group: "networking.istio.io", Kind: kind})
				if (err == nil) != tc.wantMetadata {
					t.Fatalf("%s metadata error=%v expected=%v", kind, err, tc.wantMetadata)
				}
			}
			if tc.wantMetadata {
				// Exercise live lookup for both Istio kinds from the actual cache, not just authorization arithmetic.
				for _, kind := range []string{"VirtualService", "DestinationRule"} {
					target := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "networking.istio.io/v1", "kind": kind, "metadata": map[string]any{"name": "fixture", "namespace": demo}}}
					live, err := cache.GetManagedLiveObjs([]*unstructured.Unstructured{target}, func(*Resource) bool { return false })
					if err != nil || len(live) != 1 {
						t.Fatalf("lookup %s: live=%d error=%v", kind, len(live), err)
					}
				}
			}
			// No selfsubjectaccessreviews/credential endpoints are used by disabled/normal modes.
		})
	}
}
