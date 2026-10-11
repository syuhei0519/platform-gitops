// Synthetic Argo Application observations only. No cluster API calls.
package main

import (
	"encoding/json"
	"testing"
)

func TestOrderConfigurationEvidence(t *testing.T) {
	leaves, _ := expectedLeaves(accountFixtureSHA, true, orderFixtureSHA)
	for _, leaf := range leaves[3:] {
		t.Run(leaf.name, func(t *testing.T) {
			cases := map[string][]any{
				"metadata.name": {"other", nil}, "metadata.namespace": {"other", nil},
				"metadata.deletionTimestamp": {"2026-10-11T00:00:00Z", "", false, map[string]any{}},
				"spec.project":               {"default", "", nil},
				"spec.sources":               {[]any{map[string]any{}}}, "spec.sourceHydrator": {map[string]any{}},
				"spec.ignoreDifferences":                     {[]any{map[string]any{"kind": "Deployment"}}},
				"status.sync.comparedTo":                     {nil, map[string]any{}},
				"status.sync.comparedTo.sources":             {[]any{map[string]any{}}},
				"status.sync.comparedTo.ignoreDifferences":   {[]any{map[string]any{"kind": "Deployment"}}},
				"status.operationState.syncResult.sources":   {[]any{map[string]any{}}},
				"status.operationState.syncResult.revisions": {[]any{orderFixtureSHA}},
			}
			for _, root := range []string{"spec.destination", "status.sync.comparedTo.destination"} {
				cases[root] = []any{nil, map[string]any{}}
				cases[root+".server"] = []any{"https://example.invalid", "", nil}
				cases[root+".namespace"] = []any{"other", "", nil}
				cases[root+".name"] = []any{"other-cluster"}
				cases[root+".unknown"] = []any{nil}
			}
			for _, root := range []string{"spec.source", "status.sync.comparedTo.source", "status.operationState.syncResult.source"} {
				cases[root] = []any{nil, map[string]any{}}
				cases[root+".targetRevision"] = []any{"main", accountFixtureSHA, nil}
				cases[root+".repoURL"] = []any{"https://github.com/example/other.git", "", nil}
				cases[root+".path"] = []any{"charts/other", "", nil}
				for _, k := range []string{"chart", "ref", "name", "kustomize", "plugin", "directory", "unknown"} {
					cases[root+"."+k] = []any{"override", map[string]any{}}
				}
				cases[root+".helm"] = []any{nil, map[string]any{}}
				cases[root+".helm.releaseName"] = []any{"other", "", nil}
				cases[root+".helm.valueFiles"] = []any{nil, []any{}, []any{"other.yaml"}, []any{"../../environments/local/" + leaf.resourceName + ".yaml", "override.yaml"}}
				for _, k := range []string{"parameters", "fileParameters", "apiVersions"} {
					cases[root+".helm."+k] = []any{[]any{map[string]any{"name": "enabled", "value": "false"}}, "wrong-type"}
				}
				for _, k := range []string{"values", "version", "namespace", "kubeVersion"} {
					cases[root+".helm."+k] = []any{"override", false}
				}
				cases[root+".helm.valuesObject"] = []any{map[string]any{"enabled": false}, "wrong-type"}
				for _, k := range []string{"passCredentials", "ignoreMissingValueFiles", "skipCrds", "skipTests", "skipSchemaValidation"} {
					cases[root+".helm."+k] = []any{true, "false"}
				}
				cases[root+".helm.futureOverride"] = []any{nil, false, ""}
			}
			for path, values := range cases {
				for _, value := range values {
					t.Run(path+"/"+jsonLabel(value), func(t *testing.T) {
						obj := observation(leaf)
						change(obj, path, value)
						data, _ := json.Marshal(obj)
						if leaf.ready(data) == nil {
							t.Fatalf("accepted mismatched or missing %s", path)
						}
					})
				}
			}
			// A settled but wrong configuration must fail even if all snapshots agree.
			for _, suffix := range []string{"repoURL", "path", "helm.releaseName", "helm.values"} {
				obj := observation(leaf)
				for _, root := range []string{"spec.source", "status.sync.comparedTo.source", "status.operationState.syncResult.source"} {
					change(obj, root+"."+suffix, "wrong")
				}
				data, _ := json.Marshal(obj)
				if leaf.ready(data) == nil {
					t.Fatalf("accepted mutually matching wrong %s", suffix)
				}
			}
			// Normal omitempty/neutral representations need no invented generation,
			// project or destination fields in syncResult (Argo does not record them).
			obj := observation(leaf)
			change(obj, "metadata.deletionTimestamp", nil)
			change(obj, "metadata.finalizers", []any{"resources-finalizer.argocd.argoproj.io"})
			change(obj, "spec.sources", []any{})
			for _, root := range []string{"spec.source", "status.sync.comparedTo.source", "status.operationState.syncResult.source"} {
				for key, value := range map[string]any{"parameters": []any{}, "fileParameters": nil, "apiVersions": []any{}, "valuesObject": map[string]any{}, "values": "", "version": "", "namespace": "", "kubeVersion": "", "passCredentials": false, "ignoreMissingValueFiles": false, "skipCrds": false, "skipTests": false, "skipSchemaValidation": false} {
					change(obj, root+".helm."+key, value)
				}
			}
			data, _ := json.Marshal(obj)
			if err := leaf.ready(data); err != nil {
				t.Fatal(err)
			}
		})
	}
}
func jsonLabel(value any) string { data, _ := json.Marshal(value); return string(data) }

func TestLegacyConfigurationCompatibilityAndDeletion(t *testing.T) {
	leaves, _ := expectedLeaves(accountFixtureSHA, false, "")
	for _, leaf := range leaves {
		obj := observation(leaf)
		delete(obj, "spec")
		delete(obj, "metadata")
		change(obj, "status.sync.comparedTo", nil)
		change(obj, "status.operationState.syncResult.source", nil)
		data, _ := json.Marshal(obj)
		if err := leaf.ready(data); err != nil {
			t.Fatalf("legacy status-only observation changed: %v", err)
		}
		obj["metadata"] = map[string]any{"deletionTimestamp": "2026-10-11T00:00:00Z", "finalizers": []any{"resources-finalizer.argocd.argoproj.io"}}
		data, _ = json.Marshal(obj)
		if leaf.ready(data) == nil {
			t.Fatal("accepted deleting legacy Application")
		}
	}
}
