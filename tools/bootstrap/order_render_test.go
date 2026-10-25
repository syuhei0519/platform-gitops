// 描画済みの Order Application が、配備先や参照設定の契約を満たすことを確認します。
package main

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestOrderWorkloadsContract(t *testing.T) {
	dir := os.Getenv("PE_OBSERVABILITY_RENDER_DIR")
	if dir == "" {
		t.Skip("real Helm renders are supplied by the render job")
	}
	before := docs(t, "ci/fixtures/order-p0/workloads-before.yaml")
	for _, file := range []string{"workloads-default.yaml", "workloads.yaml", "workloads-explicit-disabled.yaml"} {
		if !reflect.DeepEqual(docs(t, filepath.Join(dir, file)), before) {
			t.Fatalf("Account semantic render changed: %s", file)
		}
	}
	enabled := docs(t, filepath.Join(dir, "workloads-order-fixture.yaml"))
	if len(enabled) != 5 || !reflect.DeepEqual(enabled[:3], before) {
		t.Fatal("existing Application semantic render changed")
	}
	for i, name := range []string{"order-postgresql", "order"} {
		a := enabled[i+3]
		wave := "-10"
		if i == 1 {
			wave = "0"
		}
		want := map[string]any{
			"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
			"metadata": map[string]any{"name": "lab-local-" + name, "namespace": "argocd", "annotations": map[string]any{"argocd.argoproj.io/sync-wave": wave, "argocd.argoproj.io/sync-options": "Prune=confirm"}},
			"spec": map[string]any{
				"project":     "lab-account",
				"source":      map[string]any{"repoURL": "https://github.com/syuhei0519/application-manifest.git", "targetRevision": orderFixtureSHA, "path": "charts/" + name, "helm": map[string]any{"releaseName": name, "valueFiles": []any{"../../environments/local/" + name + ".yaml"}}},
				"destination": map[string]any{"server": "https://kubernetes.default.svc", "namespace": "account"},
				"syncPolicy":  map[string]any{"automated": map[string]any{"prune": true, "selfHeal": true, "allowEmpty": false}, "syncOptions": []any{"PruneLast=true", "FailOnSharedResource=true"}},
			},
		}
		if !reflect.DeepEqual(a, want) {
			t.Fatalf("Order topology/policy drift: %#v", a)
		}
	}
}
