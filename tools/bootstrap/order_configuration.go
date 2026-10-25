// Order の Application と設定値を照合し、無効時や有効化時の配備条件を検証します。
package main

import (
	"fmt"
	"reflect"
)

// Match the rendered single-source Order contract, including all effective Helm
// inputs. Check each snapshot against the expectation, not merely against the
// current spec: mutually consistent but wrong snapshots must fail too.
func (leaf leafExpectation) orderConfigurationReady(a app) error {
	if a.Metadata.Name != leaf.name || a.Metadata.Namespace != "argocd" || a.Spec.Project != "lab-account" {
		return fmt.Errorf("Order Application identity/project does not match the expected contract")
	}
	compared := a.Status.Sync.ComparedTo
	result := a.Status.OperationState.SyncResult
	if len(a.Spec.Sources) != 0 || len(compared.Sources) != 0 || len(result.Sources) != 0 || len(result.Revisions) != 0 ||
		len(a.Spec.IgnoreDifferences) != 0 || len(compared.IgnoreDifferences) != 0 ||
		(len(a.Spec.SourceHydrator) != 0 && string(a.Spec.SourceHydrator) != "null") {
		return fmt.Errorf("Order requires single-source comparison without hydration or ignored differences")
	}
	destination := map[string]any{"server": "https://kubernetes.default.svc", "namespace": "account"}
	for _, actual := range []map[string]any{a.Spec.Destination, compared.Destination} {
		if !contractObject(actual, destination, map[string][]any{"name": {""}}) {
			return fmt.Errorf("Order current/comparison destination does not match the expected contract")
		}
	}
	source := map[string]any{
		"repoURL":        "https://github.com/syuhei0519/application-manifest.git",
		"targetRevision": leaf.revision, "path": "charts/" + leaf.resourceName,
	}
	helm := map[string]any{"releaseName": leaf.resourceName, "valueFiles": []any{"../../environments/local/" + leaf.resourceName + ".yaml"}}
	// Argo omitempty serialization can omit neutral optional values. Accept only
	// these known neutral forms; unknown inputs require explicit contract review.
	neutralHelm := map[string][]any{
		"parameters": {nil, []any{}}, "fileParameters": {nil, []any{}}, "apiVersions": {nil, []any{}},
		"values": {""}, "valuesObject": {nil, map[string]any{}}, "version": {""}, "namespace": {""}, "kubeVersion": {""},
		"passCredentials": {false}, "ignoreMissingValueFiles": {false}, "skipCrds": {false}, "skipTests": {false}, "skipSchemaValidation": {false},
	}
	for _, actual := range []map[string]any{a.Spec.Source, compared.Source, result.Source} {
		h, ok := actual["helm"].(map[string]any)
		if !ok || !contractObject(h, helm, neutralHelm) {
			return fmt.Errorf("Order current/comparison/operation Helm settings do not match the expected contract")
		}
		expected := make(map[string]any, len(source)+1)
		for key, value := range source {
			expected[key] = value
		}
		expected["helm"] = h // Already validated, including unknown/override fields.
		if !contractObject(actual, expected, map[string][]any{
			"chart": {""}, "ref": {""}, "name": {""}, "kustomize": {nil}, "directory": {nil}, "plugin": {nil},
		}) {
			return fmt.Errorf("Order current/comparison/operation source does not match the expected contract")
		}
	}
	if result.Revision != leaf.revision {
		return fmt.Errorf("Order operation revision does not match the fixed Manifest commit")
	}
	return nil
}

// Required keys must exist with exact JSON values; optional keys are a closed
// allowlist of equivalent zero values. Missing observations are never inferred.
func contractObject(actual, required map[string]any, optional map[string][]any) bool {
	for key, value := range required {
		got, exists := actual[key]
		if !exists || !reflect.DeepEqual(got, value) {
			return false
		}
	}
	for key, value := range actual {
		if _, exists := required[key]; exists {
			continue
		}
		matched := false
		for _, neutral := range optional[key] {
			if reflect.DeepEqual(value, neutral) {
				matched = true
				break
			}
		}
		if !matched {
			return false
		}
	}
	return true
}
