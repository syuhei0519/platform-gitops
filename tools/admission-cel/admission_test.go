package admissioncel

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	celgo "github.com/google/cel-go/cel"
	celtypes "github.com/google/cel-go/common/types"
	admissionv1 "k8s.io/api/admission/v1"
	registrationv1 "k8s.io/api/admissionregistration/v1"
	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	yamlutil "k8s.io/apimachinery/pkg/util/yaml"
	"k8s.io/apiserver/pkg/admission"
	plugin "k8s.io/apiserver/pkg/admission/plugin/cel"
	rules "k8s.io/apiserver/pkg/admission/plugin/webhook/predicates/rules"
	celconfig "k8s.io/apiserver/pkg/apis/cel"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/cel/environment"
)

type expression string

func (e expression) GetExpression() string      { return string(e) }
func (e expression) ReturnTypes() []*celgo.Type { return []*celgo.Type{celgo.BoolType} }
func boolean(v bool) *bool                      { return &v }
func compiler() plugin.ConditionCompiler {
	return plugin.NewConditionCompiler(environment.MustBaseEnvSet(environment.DefaultCompatibilityVersion()))
}
func compile(t *testing.T, expressions []plugin.ExpressionAccessor) plugin.ConditionEvaluator {
	t.Helper()
	ev := compiler().CompileCondition(expressions, plugin.OptionalVariableDeclarations{}, environment.NewExpressions)
	for _, err := range ev.CompilationErrors() {
		t.Fatal(err)
	}
	return ev
}
func policies(t *testing.T) (plugin.ConditionEvaluator, registrationv1.ValidatingAdmissionPolicy) {
	t.Helper()
	path := filepath.Join("..", "..", "learning", "fresh-profile", "security", "admission.yaml")

	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	dec := yamlutil.NewYAMLOrJSONDecoder(f, 4096)
	var selected registrationv1.ValidatingAdmissionPolicy
	var selectedEval plugin.ConditionEvaluator
	count := 0
	for {
		var raw json.RawMessage
		if err := dec.Decode(&raw); err == io.EOF {
			break
		} else if err != nil {
			t.Fatal(err)
		}
		var meta metav1.TypeMeta
		if err := json.Unmarshal(raw, &meta); err != nil {
			t.Fatal(err)
		}
		if meta.Kind != "ValidatingAdmissionPolicy" {
			continue
		}
		var p registrationv1.ValidatingAdmissionPolicy
		if err := json.Unmarshal(raw, &p); err != nil {
			t.Fatal(err)
		}
		ex := []plugin.ExpressionAccessor{}
		for _, v := range p.Spec.Validations {
			ex = append(ex, expression(v.Expression))
		}
		ev := compile(t, ex)
		count++
		if p.Name == "fresh-account-pod-boundary" {
			selected = p
			selectedEval = ev
		}
	}
	if count != 3 || selectedEval == nil {
		t.Fatalf("expected three compiled policies including Account, got %d", count)
	}
	return selectedEval, selected
}
func pod(ownerKind, ownerName, sa string) *corev1.Pod {
	api := "apps/v1"
	if ownerKind == "Job" {
		api = "batch/v1"
	}
	return &corev1.Pod{TypeMeta: metav1.TypeMeta{APIVersion: "v1", Kind: "Pod"}, ObjectMeta: metav1.ObjectMeta{Name: ownerName + "-test", Namespace: "fresh-account", OwnerReferences: []metav1.OwnerReference{{APIVersion: api, Kind: ownerKind, Name: ownerName, UID: "test-owner-uid", Controller: boolean(true)}}}, Spec: corev1.PodSpec{ServiceAccountName: sa, AutomountServiceAccountToken: boolean(false), Containers: []corev1.Container{{Name: "app", Image: "example.invalid/test@sha256:0000000000000000000000000000000000000000000000000000000000000000", SecurityContext: &corev1.SecurityContext{AllowPrivilegeEscalation: boolean(false)}}}}}
}
func attributes(obj runtime.Object, resource, username, op, sub string) (*admission.VersionedAttributes, *admissionv1.AdmissionRequest) {
	gvk := obj.GetObjectKind().GroupVersionKind()
	gvr := schema.GroupVersionResource{Group: gvk.Group, Version: gvk.Version, Resource: resource}
	var old runtime.Object
	if op == "UPDATE" {
		old = obj.DeepCopyObject()
	}
	attr := admission.NewAttributesRecord(obj, old, gvk, "fresh-account", "test", gvr, sub, admission.Operation(op), &metav1.CreateOptions{}, false, &user.DefaultInfo{Name: username})
	va := &admission.VersionedAttributes{Attributes: attr, VersionedObject: obj, VersionedOldObject: old, VersionedKind: gvk}
	return va, plugin.CreateAdmissionRequest(attr, metav1.GroupVersionResource(gvr), metav1.GroupVersionKind(gvk))
}
func evaluate(t *testing.T, ev plugin.ConditionEvaluator, obj runtime.Object, resource, username, op, sub string, want bool) {
	t.Helper()
	va, req := attributes(obj, resource, username, op, sub)
	results, _, err := ev.ForInput(context.Background(), va, req, plugin.OptionalVariableBindings{}, nil, celconfig.RuntimeCELCostBudget)
	if err != nil {
		t.Fatal(err)
	}
	allowed := true
	for i, r := range results {
		if r.Error != nil || r.EvalResult != celtypes.True {
			allowed = false
			if want {
				t.Errorf("validation[%d]: value=%v error=%v", i, r.EvalResult, r.Error)
			}
		}
	}
	if allowed != want {
		t.Fatalf("allowed=%v, want=%v", allowed, want)
	}
}
func TestCanonicalAdmissionAndNegativeMutations(t *testing.T) {
	ev, _ := policies(t)
	fixtures := []struct{ kind, name, sa, controller string }{{"StatefulSet", "account-postgresql", "account-database", "statefulset-controller"}, {"Job", "account-db-role-bootstrap", "account-migration", "job-controller"}, {"Job", "account-migration", "account-migration", "job-controller"}, {"ReplicaSet", "account-api-hash", "account-runtime", "replicaset-controller"}}
	for _, f := range fixtures {
		t.Run(f.name, func(t *testing.T) {
			p := pod(f.kind, f.name, f.sa)
			username := "system:serviceaccount:kube-system:" + f.controller
			for _, op := range []string{"CREATE", "UPDATE"} {
				t.Run(op, func(t *testing.T) { evaluate(t, ev, p, "pods", username, op, "", true) })
			}
			for _, wrong := range []string{"system:kube-controller-manager", "system:serviceaccount:fresh-account:account-runtime", "system:serviceaccount:kube-system:deployment-controller", "system:serviceaccount:kube-system:statefulset-controller", "system:serviceaccount:kube-system:job-controller", "system:serviceaccount:kube-system:replicaset-controller"} {
				if wrong == username {
					continue
				}
				t.Run("wrong-identity/"+wrong, func(t *testing.T) { evaluate(t, ev, p, "pods", wrong, "CREATE", "", false) })
			}
			evaluate(t, ev, p, "pods", username, "UPDATE", "ephemeralcontainers", false)
			mutations := map[string]func(*corev1.Pod){
				"omitted-context":  func(p *corev1.Pod) { p.Spec.Containers[0].SecurityContext = nil },
				"empty-context":    func(p *corev1.Pod) { p.Spec.Containers[0].SecurityContext = &corev1.SecurityContext{} },
				"escalation":       func(p *corev1.Pod) { p.Spec.Containers[0].SecurityContext.AllowPrivilegeEscalation = boolean(true) },
				"privileged":       func(p *corev1.Pod) { p.Spec.Containers[0].SecurityContext.Privileged = boolean(true) },
				"host-network":     func(p *corev1.Pod) { p.Spec.HostNetwork = true },
				"automount":        func(p *corev1.Pod) { p.Spec.AutomountServiceAccountToken = boolean(true) },
				"wrong-owner":      func(p *corev1.Pod) { p.OwnerReferences[0].Name = "other" },
				"no-owner":         func(p *corev1.Pod) { p.OwnerReferences = nil },
				"no-owner-uid":     func(p *corev1.Pod) { p.OwnerReferences[0].UID = "" },
				"wrong-owner-kind": func(p *corev1.Pod) { p.OwnerReferences[0].Kind = "Deployment" },
				"secret-envfrom": func(p *corev1.Pod) {
					p.Spec.Containers[0].EnvFrom = []corev1.EnvFromSource{{SecretRef: &corev1.SecretEnvSource{LocalObjectReference: corev1.LocalObjectReference{Name: "other"}}}}
				},
				"secret-volume": func(p *corev1.Pod) {
					p.Spec.Volumes = []corev1.Volume{{Name: "secret", VolumeSource: corev1.VolumeSource{Secret: &corev1.SecretVolumeSource{SecretName: "other"}}}}
				},
				"bad-init": func(p *corev1.Pod) { p.Spec.InitContainers = []corev1.Container{{Name: "init", Image: "test"}} },
				"ephemeral-container": func(p *corev1.Pod) {
					p.Spec.EphemeralContainers = []corev1.EphemeralContainer{{EphemeralContainerCommon: corev1.EphemeralContainerCommon{Name: "debug", Image: "test"}}}
				},
			}
			for name, mutate := range mutations {
				t.Run(name, func(t *testing.T) {
					bad := p.DeepCopy()
					mutate(bad)
					evaluate(t, ev, bad, "pods", username, "CREATE", "", false)
				})
			}
			safeInit := p.DeepCopy()
			safeInit.Spec.InitContainers = []corev1.Container{{Name: "safe-init", Image: "test", SecurityContext: &corev1.SecurityContext{AllowPrivilegeEscalation: boolean(false)}}}
			evaluate(t, ev, safeInit, "pods", username, "CREATE", "", true)
			if f.kind == "StatefulSet" {
				obj := &appsv1.StatefulSet{TypeMeta: metav1.TypeMeta{APIVersion: "apps/v1", Kind: "StatefulSet"}, ObjectMeta: metav1.ObjectMeta{Name: f.name, Namespace: "fresh-account"}, Spec: appsv1.StatefulSetSpec{Template: corev1.PodTemplateSpec{Spec: p.Spec}}}
				for _, op := range []string{"CREATE", "UPDATE"} {
					evaluate(t, ev, obj, "statefulsets", "argocd", op, "", true)
				}
			}
			if f.kind == "Job" {
				obj := &batchv1.Job{TypeMeta: metav1.TypeMeta{APIVersion: "batch/v1", Kind: "Job"}, ObjectMeta: metav1.ObjectMeta{Name: f.name, Namespace: "fresh-account"}, Spec: batchv1.JobSpec{Template: corev1.PodTemplateSpec{Spec: p.Spec}}}
				for _, op := range []string{"CREATE", "UPDATE"} {
					evaluate(t, ev, obj, "jobs", "argocd", op, "", true)
				}
			}
		})
	}
}
func TestAdmissionRequestOmitEmptyAndRuleMatching(t *testing.T) {
	ev, policy := policies(t)
	p := pod("StatefulSet", "account-postgresql", "account-database")
	username := "system:serviceaccount:kube-system:statefulset-controller"
	for _, raw := range []string{`{}`, `{"subResource":""}`} {
		var req admissionv1.AdmissionRequest
		if err := json.Unmarshal([]byte(raw), &req); err != nil {
			t.Fatal(err)
		}
		m, err := runtime.DefaultUnstructuredConverter.ToUnstructured(&req)
		if err != nil {
			t.Fatal(err)
		}
		if _, present := m["subResource"]; present {
			t.Fatalf("empty subResource must be absent after actual conversion: %s", raw)
		}
	}
	for _, tc := range []struct {
		sub          string
		match, allow bool
	}{{"", true, true}, {"ephemeralcontainers", true, false}, {"status", false, true}} {
		va, _ := attributes(p, "pods", username, "UPDATE", tc.sub)
		matched := false
		for _, rule := range policy.Spec.MatchConstraints.ResourceRules {
			matcher := rules.Matcher{Rule: rule.RuleWithOperations, Attr: va.Attributes}
			matched = matched || matcher.Matches()
		}
		if matched != tc.match {
			t.Fatalf("subresource %q match=%v want=%v", tc.sub, matched, tc.match)
		}
		evaluate(t, ev, p, "pods", username, "UPDATE", tc.sub, tc.allow)
	}
	// Lock the failure reproduction: old unguarded CEL must error on real omitted field.
	old := compile(t, []plugin.ExpressionAccessor{expression("request.subResource != 'ephemeralcontainers'")})
	va, req := attributes(p, "pods", username, "CREATE", "")
	results, _, err := old.ForInput(context.Background(), va, req, plugin.OptionalVariableBindings{}, nil, celconfig.RuntimeCELCostBudget)
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 1 || results[0].Error == nil {
		t.Fatal("expected historical missing subResource error")
	}
}

// Evaluate distinct old/new Rollouts through the Kubernetes admission CEL engine.
func TestFreshRolloutModeTransition(t *testing.T) {
	_, policy := policies(t)
	// Isolate the actual mode rule; the rest of the policy is covered above.
	rule := compile(t, []plugin.ExpressionAccessor{expression(policy.Spec.Validations[len(policy.Spec.Validations)-1].Expression)})
	makeObj := func() *unstructured.Unstructured {
		return &unstructured.Unstructured{Object: map[string]interface{}{
			"apiVersion": "argoproj.io/v1alpha1", "kind": "Rollout", "metadata": map[string]interface{}{"name": "account-api", "namespace": "fresh-account", "generation": int64(3)},
			"spec":   map[string]interface{}{"strategy": map[string]interface{}{"canary": map[string]interface{}{"steps": []interface{}{map[string]interface{}{"setWeight": int64(10)}, map[string]interface{}{"pause": map[string]interface{}{}}}}}, "template": map[string]interface{}{"spec": map[string]interface{}{"containers": []interface{}{map[string]interface{}{"name": "api", "image": "old"}}}}},
			"status": map[string]interface{}{"phase": "Healthy", "observedGeneration": "3", "stableRS": "aaaaa", "currentPodHash": "aaaaa"},
		}}
	}
	for _, tc := range []struct {
		name, phase, observed, current    string
		templateChange, stepsChange, want bool
	}{
		{"stable-switch", "Healthy", "3", "aaaaa", false, true, true},
		{"in-progress", "Progressing", "3", "bbbbb", false, true, false},
		{"stale-status", "Healthy", "2", "aaaaa", false, true, false},
		{"hash-mismatch", "Healthy", "3", "bbbbb", false, true, false},
		{"simultaneous-template-change", "Healthy", "3", "aaaaa", true, true, false},
		{"unchanged-steps", "Progressing", "3", "bbbbb", false, false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			old := makeObj()
			next := old.DeepCopy()
			st := old.Object["status"].(map[string]interface{})
			st["phase"] = tc.phase
			st["observedGeneration"] = tc.observed
			st["currentPodHash"] = tc.current
			if tc.stepsChange {
				next.Object["spec"].(map[string]interface{})["strategy"].(map[string]interface{})["canary"].(map[string]interface{})["steps"] = []interface{}{map[string]interface{}{"setWeight": int64(10)}}
			}
			if tc.templateChange {
				next.Object["spec"].(map[string]interface{})["template"].(map[string]interface{})["metadata"] = map[string]interface{}{"labels": map[string]interface{}{"test": "changed"}}
			}
			gvk := schema.GroupVersionKind{Group: "argoproj.io", Version: "v1alpha1", Kind: "Rollout"}
			gvr := schema.GroupVersionResource{Group: "argoproj.io", Version: "v1alpha1", Resource: "rollouts"}
			attr := admission.NewAttributesRecord(next, old, gvk, "fresh-account", "account-api", gvr, "", admission.Update, &metav1.UpdateOptions{}, false, &user.DefaultInfo{Name: "argocd"})
			va := &admission.VersionedAttributes{Attributes: attr, VersionedObject: next, VersionedOldObject: old, VersionedKind: gvk}
			req := plugin.CreateAdmissionRequest(attr, metav1.GroupVersionResource(gvr), metav1.GroupVersionKind(gvk))
			results, _, err := rule.ForInput(context.Background(), va, req, plugin.OptionalVariableBindings{}, nil, celconfig.RuntimeCELCostBudget)
			if err != nil {
				t.Fatal(err)
			}
			if len(results) != 1 || results[0].Error != nil {
				t.Fatalf("CEL evaluation: %+v", results)
			}
			if (results[0].EvalResult == celtypes.True) != tc.want {
				t.Fatalf("result=%v want=%v", results[0].EvalResult, tc.want)
			}
		})
	}
}

func TestApprovedPromotionParameter(t *testing.T) {
	f, err := os.Open(filepath.Join("..", "..", "learning", "fresh-profile", "security", "admission.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	d := yamlutil.NewYAMLOrJSONDecoder(f, 4096)
	var chosen registrationv1.ValidatingAdmissionPolicy
	for {
		var raw json.RawMessage
		e := d.Decode(&raw)
		if e == io.EOF {
			break
		}
		if e != nil {
			t.Fatal(e)
		}
		var meta metav1.TypeMeta
		_ = json.Unmarshal(raw, &meta)
		if meta.Kind != "ValidatingAdmissionPolicy" {
			continue
		}
		var p registrationv1.ValidatingAdmissionPolicy
		if e = json.Unmarshal(raw, &p); e != nil {
			t.Fatal(e)
		}
		if p.Name == "fresh-application-boundary" {
			chosen = p
			break
		}
	}
	if chosen.Name == "" {
		t.Fatal("application policy missing")
	}
	app := &unstructured.Unstructured{Object: map[string]interface{}{"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "metadata": map[string]interface{}{"name": "fresh-release-definitions", "namespace": "fresh-control"}, "spec": map[string]interface{}{"project": "fresh-platform", "destination": map[string]interface{}{"server": "https://kubernetes.default.svc", "namespace": "fresh-control"}, "source": map[string]interface{}{"repoURL": "https://github.com/syuhei0519/application-manifest.git", "targetRevision": "3707f73581da5d04db0e4771dda7c1e82bd0f47c", "path": "learning/fresh-profile/release-definition", "helm": map[string]interface{}{"releaseName": "fresh-account-release-definition", "parameters": []interface{}{map[string]interface{}{"name": "workloadRevision", "value": "3707f73581da5d04db0e4771dda7c1e82bd0f47c"}, map[string]interface{}{"name": "accountRuntimeImage", "value": "ghcr.io/syuhei0519/backend-app@sha256:fba8eae42982a0e818581b2ac6b41ba865920dafdbfd7d48c416f98c5b1eaebd"}, map[string]interface{}{"name": "promotionMode", "value": "manual"}, map[string]interface{}{"name": "syncMode", "value": "automated"}, map[string]interface{}{"name": "workloadVariant", "value": "fresh"}}}}}}}
	for _, tc := range []struct {
		name, pinned, mode string
		duplicate, want    bool
	}{{"manual-approved", "manual", "manual", false, true}, {"auto-unapproved", "manual", "auto", false, false}, {"unknown", "manual", "other", false, false}, {"duplicate", "manual", "manual", true, false}, {"auto-reviewed", "auto", "auto", false, true}, {"manual-after-auto", "auto", "manual", false, false}} {
		t.Run(tc.name, func(t *testing.T) {
			exprs := []plugin.ExpressionAccessor{}
			for _, v := range chosen.Spec.Validations {
				expr := strings.ReplaceAll(v.Expression, "p.name == 'promotionMode' && p.value == 'manual'", "p.name == 'promotionMode' && p.value == '"+tc.pinned+"'")
				exprs = append(exprs, expression(expr))
			}
			ev := compile(t, exprs)
			obj := app.DeepCopy()
			args := obj.Object["spec"].(map[string]interface{})["source"].(map[string]interface{})["helm"].(map[string]interface{})["parameters"].([]interface{})
			args[2].(map[string]interface{})["value"] = tc.mode
			if tc.duplicate {
				args[2] = map[string]interface{}{"name": "workloadRevision", "value": "3707f73581da5d04db0e4771dda7c1e82bd0f47c"}
			}
			gvk := schema.GroupVersionKind{Group: "argoproj.io", Version: "v1alpha1", Kind: "Application"}
			gvr := schema.GroupVersionResource{Group: "argoproj.io", Version: "v1alpha1", Resource: "applications"}
			attr := admission.NewAttributesRecord(obj, obj.DeepCopy(), gvk, "fresh-control", "fresh-release-definitions", gvr, "", admission.Update, &metav1.UpdateOptions{}, false, &user.DefaultInfo{Name: "argocd"})
			va := &admission.VersionedAttributes{Attributes: attr, VersionedObject: obj, VersionedOldObject: obj.DeepCopy(), VersionedKind: gvk}
			req := plugin.CreateAdmissionRequest(attr, metav1.GroupVersionResource(gvr), metav1.GroupVersionKind(gvk))
			res, _, err := ev.ForInput(context.Background(), va, req, plugin.OptionalVariableBindings{}, nil, celconfig.RuntimeCELCostBudget)
			if err != nil {
				t.Fatal(err)
			}
			allowed := true
			for _, r := range res {
				if r.Error != nil || r.EvalResult != celtypes.True {
					allowed = false
					if tc.want {
						t.Errorf("value=%v error=%v", r.EvalResult, r.Error)
					}
				}
			}
			if allowed != tc.want {
				t.Fatalf("allowed=%v want=%v", allowed, tc.want)
			}
		})
	}
	for _, tc := range []struct {
		name, pinned, mode string
		duplicate, want    bool
	}{
		{"sync-automated-approved", "automated", "automated", false, true},
		{"sync-paused-unapproved", "automated", "paused", false, false},
		{"sync-invalid", "automated", "unexpected", false, false},
		{"sync-duplicate", "automated", "automated", true, false},
		{"sync-paused-reviewed", "paused", "paused", false, true},
		{"sync-automated-after-paused-review", "paused", "automated", false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			exprs := []plugin.ExpressionAccessor{}
			for _, v := range chosen.Spec.Validations {
				expr := strings.ReplaceAll(v.Expression, "p.name == 'syncMode' && p.value == 'automated'", "p.name == 'syncMode' && p.value == '"+tc.pinned+"'")
				exprs = append(exprs, expression(expr))
			}
			ev := compile(t, exprs)
			obj := app.DeepCopy()
			args := obj.Object["spec"].(map[string]interface{})["source"].(map[string]interface{})["helm"].(map[string]interface{})["parameters"].([]interface{})
			args[3].(map[string]interface{})["value"] = tc.mode
			if tc.duplicate {
				args[3] = map[string]interface{}{"name": "promotionMode", "value": "manual"}
			}
			gvk := schema.GroupVersionKind{Group: "argoproj.io", Version: "v1alpha1", Kind: "Application"}
			gvr := schema.GroupVersionResource{Group: "argoproj.io", Version: "v1alpha1", Resource: "applications"}
			attr := admission.NewAttributesRecord(obj, obj.DeepCopy(), gvk, "fresh-control", "fresh-release-definitions", gvr, "", admission.Update, &metav1.UpdateOptions{}, false, &user.DefaultInfo{Name: "argocd"})
			va := &admission.VersionedAttributes{Attributes: attr, VersionedObject: obj, VersionedOldObject: obj.DeepCopy(), VersionedKind: gvk}
			req := plugin.CreateAdmissionRequest(attr, metav1.GroupVersionResource(gvr), metav1.GroupVersionKind(gvk))
			res, _, err := ev.ForInput(context.Background(), va, req, plugin.OptionalVariableBindings{}, nil, celconfig.RuntimeCELCostBudget)
			if err != nil {
				t.Fatal(err)
			}
			allowed := true
			for _, r := range res {
				if r.Error != nil || r.EvalResult != celtypes.True {
					allowed = false
					if tc.want {
						t.Errorf("value=%v error=%v", r.EvalResult, r.Error)
					}
				}
			}
			if allowed != tc.want {
				t.Fatalf("allowed=%v want=%v", allowed, tc.want)
			}
		})
	}

	for _, stage := range []struct{ name, platform, chart, workload, mode, variant, path, promotion string }{
		{"initial", strings.Repeat("1", 40), strings.Repeat("2", 40), strings.Repeat("3", 40), "paused", "fresh", "learning/fresh-profile/workload", "manual"},
		{"new-paused", strings.Repeat("4", 40), strings.Repeat("5", 40), strings.Repeat("6", 40), "paused", "existing-upgrade", "learning/fresh-profile/workload-existing", "manual"},
		{"final-automated", strings.Repeat("7", 40), strings.Repeat("8", 40), strings.Repeat("9", 40), "automated", "existing-upgrade", "learning/fresh-profile/workload-existing", "manual"},
		{"rollback-old", strings.Repeat("a", 40), strings.Repeat("b", 40), strings.Repeat("c", 40), "automated", "fresh", "learning/fresh-profile/workload", "manual"},
		{"fresh-auto", strings.Repeat("1", 40), strings.Repeat("2", 40), strings.Repeat("3", 40), "paused", "fresh", "learning/fresh-profile/workload-auto", "auto"},
		{"existing-auto", strings.Repeat("4", 40), strings.Repeat("5", 40), strings.Repeat("6", 40), "paused", "existing-upgrade", "learning/fresh-profile/workload-existing-auto", "auto"},
		{"existing-auto-final", strings.Repeat("4", 40), strings.Repeat("5", 40), strings.Repeat("6", 40), "automated", "existing-upgrade", "learning/fresh-profile/workload-existing-auto", "auto"},
	} {
		t.Run("policy-stage-"+stage.name, func(t *testing.T) {
			exprs := []plugin.ExpressionAccessor{}
			old := "3707f73581da5d04db0e4771dda7c1e82bd0f47c"
			for _, validation := range chosen.Spec.Validations {
				expr := strings.ReplaceAll(validation.Expression, "__PLATFORM_GITOPS_SHA__", stage.platform)
				needle := "object.spec.source.targetRevision == '" + old + "'"
				expr = strings.Replace(expr, needle, "object.spec.source.targetRevision == '"+stage.chart+"'", 1)
				expr = strings.Replace(expr, needle, "object.spec.source.targetRevision == '"+stage.workload+"'", 1)
				expr = strings.ReplaceAll(expr, "p.name == 'workloadRevision' && p.value == '"+old+"'", "p.name == 'workloadRevision' && p.value == '"+stage.workload+"'")
				expr = strings.ReplaceAll(expr, "p.name == 'promotionMode' && p.value == 'manual'", "p.name == 'promotionMode' && p.value == '"+stage.promotion+"'")
				expr = strings.ReplaceAll(expr, "p.name == 'syncMode' && p.value == 'automated'", "p.name == 'syncMode' && p.value == '"+stage.mode+"'")
				expr = strings.ReplaceAll(expr, "p.name == 'workloadVariant' && p.value == 'fresh'", "p.name == 'workloadVariant' && p.value == '"+stage.variant+"'")
				expr = strings.ReplaceAll(expr, "object.spec.source.path == 'learning/fresh-profile/workload'", "object.spec.source.path == '"+stage.path+"'")
				exprs = append(exprs, expression(expr))
			}
			ev := compile(t, exprs)
			parent := app.DeepCopy()
			source := parent.Object["spec"].(map[string]interface{})["source"].(map[string]interface{})
			source["targetRevision"] = stage.chart
			args := source["helm"].(map[string]interface{})["parameters"].([]interface{})
			args[0].(map[string]interface{})["value"] = stage.workload
			args[2].(map[string]interface{})["value"] = stage.promotion
			args[3].(map[string]interface{})["value"] = stage.mode
			args[4].(map[string]interface{})["value"] = stage.variant
			child := &unstructured.Unstructured{Object: map[string]interface{}{"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "metadata": map[string]interface{}{"name": "fresh-account-release", "namespace": "fresh-control"}, "spec": map[string]interface{}{"project": "fresh-account", "destination": map[string]interface{}{"server": "https://kubernetes.default.svc", "namespace": "fresh-account"}, "source": map[string]interface{}{"repoURL": "https://github.com/syuhei0519/application-manifest.git", "targetRevision": stage.workload, "path": stage.path, "kustomize": map[string]interface{}{"images": []interface{}{"ghcr.io/syuhei0519/backend-app=ghcr.io/syuhei0519/backend-app@sha256:fba8eae42982a0e818581b2ac6b41ba865920dafdbfd7d48c416f98c5b1eaebd"}}}}}}
			root := &unstructured.Unstructured{Object: map[string]interface{}{"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "metadata": map[string]interface{}{"name": "account-lightweight-root", "namespace": "fresh-control"}, "spec": map[string]interface{}{"project": "fresh-platform", "destination": map[string]interface{}{"server": "https://kubernetes.default.svc", "namespace": "fresh-control"}, "source": map[string]interface{}{"repoURL": "https://github.com/syuhei0519/platform-gitops.git", "targetRevision": stage.platform, "path": "learning/fresh-profile/root/children", "directory": map[string]interface{}{"recurse": true}}}}}
			check := func(obj *unstructured.Unstructured) bool {
				gvk := schema.GroupVersionKind{Group: "argoproj.io", Version: "v1alpha1", Kind: "Application"}
				gvr := schema.GroupVersionResource{Group: "argoproj.io", Version: "v1alpha1", Resource: "applications"}
				attr := admission.NewAttributesRecord(obj, obj.DeepCopy(), gvk, "fresh-control", obj.GetName(), gvr, "", admission.Update, &metav1.UpdateOptions{}, false, &user.DefaultInfo{Name: "argocd"})
				va := &admission.VersionedAttributes{Attributes: attr, VersionedObject: obj, VersionedOldObject: obj.DeepCopy(), VersionedKind: gvk}
				req := plugin.CreateAdmissionRequest(attr, metav1.GroupVersionResource(gvr), metav1.GroupVersionKind(gvk))
				res, _, err := ev.ForInput(context.Background(), va, req, plugin.OptionalVariableBindings{}, nil, celconfig.RuntimeCELCostBudget)
				if err != nil {
					t.Fatal(err)
				}
				for _, r := range res {
					if r.Error != nil || r.EvalResult != celtypes.True {
						return false
					}
				}
				return true
			}
			for _, obj := range []*unstructured.Unstructured{root, parent, child} {
				if !check(obj) {
					t.Errorf("reviewed %s rejected", obj.GetName())
				}
				bad := obj.DeepCopy()
				bad.Object["spec"].(map[string]interface{})["source"].(map[string]interface{})["targetRevision"] = strings.Repeat("f", 40)
				if check(bad) {
					t.Errorf("unapproved revision accepted for %s", obj.GetName())
				}
			}
			badMode := parent.DeepCopy()
			badParams := badMode.Object["spec"].(map[string]interface{})["source"].(map[string]interface{})["helm"].(map[string]interface{})["parameters"].([]interface{})
			if stage.promotion == "auto" {
				badParams[2].(map[string]interface{})["value"] = "manual"
			} else {
				badParams[2].(map[string]interface{})["value"] = "auto"
			}
			if check(badMode) {
				t.Error("unapproved promotion mode accepted")
			}
			bad := child.DeepCopy()
			bad.Object["spec"].(map[string]interface{})["source"].(map[string]interface{})["path"] = "learning/fresh-profile/other"
			if check(bad) {
				t.Error("unapproved path accepted")
			}
			bad = parent.DeepCopy()
			bad.Object["spec"].(map[string]interface{})["destination"].(map[string]interface{})["namespace"] = "other"
			if check(bad) {
				t.Error("unapproved namespace accepted")
			}
			bad = parent.DeepCopy()
			bad.SetName("unapproved-application")
			if check(bad) {
				t.Error("unapproved application accepted")
			}
		})
	}

}
