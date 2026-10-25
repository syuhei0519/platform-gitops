// SonarQube 管理に必要な Argo CD cache の読み取りと権限境界を確認します。
// Injected only into a disposable checkout of the fixed Argo CD GitOps Engine.
package cache

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/argoproj/argo-cd/gitops-engine/pkg/utils/kube"
	"github.com/argoproj/argo-cd/gitops-engine/pkg/utils/kube/kubetest"
	authorizationv1 "k8s.io/api/authorization/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic/fake"
	"k8s.io/client-go/rest"
	ktesting "k8s.io/client-go/testing"
)

func TestSonarNamespaceCacheBoundary(t *testing.T) {
	const old = "learning-services-20261018"
	const sonar = "sonarqube"
	resources := []struct{ group, kind, plural string }{
		{"", "Secret", "secrets"}, {"", "ConfigMap", "configmaps"},
		{"", "PersistentVolumeClaim", "persistentvolumeclaims"},
		{"", "ServiceAccount", "serviceaccounts"}, {"apps", "StatefulSet", "statefulsets"},
		{"networking.k8s.io", "NetworkPolicy", "networkpolicies"},
		{"", "Service", "services"}, {"", "Pod", "pods"},
	}
	allowed := func(ns, group, resource string) bool {
		if ns == sonar {
			for _, r := range resources {
				if r.group == group && r.plural == resource {
					return true
				}
			}
		}
		return ns == old && group == "" && (resource == "services" || resource == "pods")
	}
	// Strict mode uses the real Engine's HTTP SelfSubjectAccessReview implementation.
	// This fake API grants exactly the selected namespaced read permissions above.
	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || r.URL.Path != "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews" {
			t.Errorf("unexpected API request %s %s", r.Method, r.URL.Path)
			http.Error(w, "unexpected", 404)
			return
		}
		var review authorizationv1.SelfSubjectAccessReview
		if err := json.NewDecoder(r.Body).Decode(&review); err != nil {
			t.Error(err)
			http.Error(w, "invalid", 400)
			return
		}
		a := review.Spec.ResourceAttributes
		review.TypeMeta = metav1.TypeMeta{APIVersion: "authorization.k8s.io/v1", Kind: "SelfSubjectAccessReview"}
		review.Status.Allowed = a != nil && a.Verb == "list" && allowed(a.Namespace, a.Group, a.Resource)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(review)
	}))
	defer api.Close()
	for _, tc := range []struct {
		name                          string
		namespaces                    []string
		mode                          int
		wantError, wantSecretMetadata bool
	}{
		{"union-default-fails-forbidden", []string{old, sonar}, RespectRbacDisabled, true, false},
		{"union-normal-loses-secret-metadata", []string{old, sonar}, RespectRbacNormal, false, false},
		{"union-strict-still-fails-forbidden", []string{old, sonar}, RespectRbacStrict, true, false},
		{"sonar-only-control-not-an-approved-scope-change", []string{sonar}, RespectRbacDisabled, false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			apis := []kube.APIResourceInfo{}
			lists := map[schema.GroupVersionResource]string{}
			objects := []runtime.Object{}
			for _, r := range resources {
				gvr := schema.GroupVersionResource{Group: r.group, Version: "v1", Resource: r.plural}
				lists[gvr] = r.kind + "List"
				apis = append(apis, kube.APIResourceInfo{GroupKind: schema.GroupKind{Group: r.group, Kind: r.kind}, GroupVersionResource: gvr, Meta: metav1.APIResource{Name: r.plural, Kind: r.kind, Namespaced: true}})
				objects = append(objects, &unstructured.Unstructured{Object: map[string]any{"apiVersion": gvr.GroupVersion().String(), "kind": r.kind, "metadata": map[string]any{"name": "fixture", "namespace": sonar, "resourceVersion": "1"}}})
			}
			client := fake.NewSimpleDynamicClientWithCustomListKinds(runtime.NewScheme(), lists, objects...)
			client.PrependReactor("list", "*", func(a ktesting.Action) (bool, runtime.Object, error) {
				if !allowed(a.GetNamespace(), a.GetResource().Group, a.GetResource().Resource) {
					return true, nil, apierrors.NewForbidden(a.GetResource().GroupResource(), "", nil)
				}
				return false, nil, nil
			})
			c := NewClusterCache(&rest.Config{Host: api.URL, ContentConfig: rest.ContentConfig{ContentType: "application/json", AcceptContentTypes: "application/json"}}, SetKubectl(&kubetest.MockKubectlCmd{APIResources: apis, DynamicClient: client}), SetNamespaces(tc.namespaces), SetRespectRBAC(tc.mode), SetWatchResyncTimeout(time.Hour), SetPopulateResourceInfoHandler(func(*unstructured.Unstructured, bool) (any, bool) { return nil, true }))
			defer c.Invalidate()
			err := c.EnsureSynced()
			if (err != nil) != tc.wantError {
				t.Fatalf("EnsureSynced error=%v wantError=%v", err, tc.wantError)
			}
			if tc.wantError {
				if !strings.Contains(err.Error(), "failed to load initial state") || !strings.Contains(err.Error(), "is forbidden") || strings.Contains(err.Error(), "failed to check permissions") {
					t.Fatalf("unexpected failure instead of the intended namespace denial: %v", err)
				}
				t.Logf("expected cache rejection: %v", err)
				return
			}
			_, err = c.IsNamespaced(schema.GroupKind{Kind: "Secret"})
			if (err == nil) != tc.wantSecretMetadata {
				t.Fatalf("Secret metadata error=%v wantMetadata=%v", err, tc.wantSecretMetadata)
			}
			if tc.wantSecretMetadata {
				obj := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "v1", "kind": "Secret", "metadata": map[string]any{"name": "fixture", "namespace": sonar}}}
				live, err := c.GetManagedLiveObjs([]*unstructured.Unstructured{obj}, func(*Resource) bool { return false })
				if err != nil || len(live) != 1 {
					t.Fatalf("Secret lookup: live=%d err=%v", len(live), err)
				}
			}
		})
	}
}

// The controller keys its cache by the exact server URL. This checks the Engine
// half of a distinct-URL proposal, given already filtered discovery results.
// It does NOT test cluster registration, the real resource filter or credentials.
func TestSonarEndpointSplitCache(t *testing.T) {
	const old = "learning-services-20261018"
	const sonar = "sonarqube"
	resources := []struct{ group, kind, plural string }{
		{"", "Secret", "secrets"}, {"", "ConfigMap", "configmaps"},
		{"", "PersistentVolumeClaim", "persistentvolumeclaims"},
		{"", "ServiceAccount", "serviceaccounts"}, {"apps", "StatefulSet", "statefulsets"},
		{"networking.k8s.io", "NetworkPolicy", "networkpolicies"},
		{"", "Service", "services"}, {"", "Pod", "pods"},
	}
	lists := map[schema.GroupVersionResource]string{}
	objects := []runtime.Object{}
	apis := []kube.APIResourceInfo{}
	for _, r := range resources {
		gvr := schema.GroupVersionResource{Group: r.group, Version: "v1", Resource: r.plural}
		lists[gvr] = r.kind + "List"
		apis = append(apis, kube.APIResourceInfo{GroupKind: schema.GroupKind{Group: r.group, Kind: r.kind}, GroupVersionResource: gvr, Meta: metav1.APIResource{Name: r.plural, Kind: r.kind, Namespaced: true}})
		for _, ns := range []string{old, sonar} {
			if ns == old && r.kind != "Service" && r.kind != "Pod" {
				continue
			}
			objects = append(objects, &unstructured.Unstructured{Object: map[string]any{"apiVersion": gvr.GroupVersion().String(), "kind": r.kind, "metadata": map[string]any{"name": "fixture", "namespace": ns, "resourceVersion": "1"}}})
		}
	}
	client := fake.NewSimpleDynamicClientWithCustomListKinds(runtime.NewScheme(), lists, objects...)
	client.PrependReactor("list", "*", func(a ktesting.Action) (bool, runtime.Object, error) {
		if a.GetNamespace() == old && a.GetResource().Resource != "services" && a.GetResource().Resource != "pods" {
			t.Errorf("old destination must not gain list access to %s", a.GetResource())
			return true, nil, apierrors.NewForbidden(a.GetResource().GroupResource(), "", nil)
		}
		return false, nil, nil
	})
	for _, tc := range []struct{ name, server, namespace, kind string }{
		{"old-destination-unchanged", "https://kubernetes.default.svc", old, "Service"},
		{"sonar-distinct-url", "https://kubernetes.default.svc:443", sonar, "Secret"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// MockKubectlCmd does not implement filtering. Simulate only the
			// discovery output of cluster-URL-scoped inclusions, explicitly.
			visible := []kube.APIResourceInfo{}
			for _, api := range apis {
				if tc.namespace == sonar || api.GroupKind.Kind == "Service" || api.GroupKind.Kind == "Pod" {
					visible = append(visible, api)
				}
			}
			c := NewClusterCache(&rest.Config{Host: tc.server}, SetKubectl(&kubetest.MockKubectlCmd{APIResources: visible, DynamicClient: client}), SetNamespaces([]string{tc.namespace}), SetRespectRBAC(RespectRbacDisabled), SetWatchResyncTimeout(time.Hour), SetPopulateResourceInfoHandler(func(*unstructured.Unstructured, bool) (any, bool) { return nil, true }))
			defer c.Invalidate()
			if err := c.EnsureSynced(); err != nil {
				t.Fatal(err)
			}
			if _, err := c.IsNamespaced(schema.GroupKind{Kind: tc.kind}); err != nil {
				t.Fatal(err)
			}
			obj := &unstructured.Unstructured{Object: map[string]any{"apiVersion": "v1", "kind": tc.kind, "metadata": map[string]any{"name": "fixture", "namespace": tc.namespace}}}
			live, err := c.GetManagedLiveObjs([]*unstructured.Unstructured{obj}, func(*Resource) bool { return false })
			if err != nil || len(live) != 1 {
				t.Fatalf("live lookup %s/%s: count=%d err=%v", tc.namespace, tc.kind, len(live), err)
			}
		})
	}
}
