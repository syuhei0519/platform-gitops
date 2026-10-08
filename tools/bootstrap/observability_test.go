// 明示scrape・RBAC・資源予算など観測構成契約をrender入力から確認する。
// 設定値は開始予算であり、テスト成功だけで実環境の容量余裕を保証しない。
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

func object(v any) map[string]any { m, _ := v.(map[string]any); return m }
func items(v any) []any           { a, _ := v.([]any); return a }
func path(v any, keys ...string) any {
	for _, k := range keys {
		v = object(v)[k]
	}
	return v
}
func docs(t *testing.T, file string) []map[string]any {
	t.Helper()
	f, err := os.Open(file)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	dec := yaml.NewDecoder(f)
	var out []map[string]any
	for {
		var d map[string]any
		err = dec.Decode(&d)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if len(d) > 0 {
			out = append(out, d)
		}
	}
	return out
}
func hasWildcard(v any) bool {
	switch x := v.(type) {
	case string:
		return x == "*"
	case map[string]any:
		for _, e := range x {
			if hasWildcard(e) {
				return true
			}
		}
	case []any:
		for _, e := range x {
			if hasWildcard(e) {
				return true
			}
		}
	}
	return false
}

func TestObservabilityContract(t *testing.T) {
	dir := os.Getenv("PE_OBSERVABILITY_RENDER_DIR")
	if dir == "" {
		t.Skip("real Helm renders are supplied by the render job")
	}
	foundation := docs(t, filepath.Join(dir, "observability-foundation.yaml"))
	if len(foundation) != 8 {
		t.Fatalf("foundation must have eight owned resources including credential-free Collector/Grafana SAs, got %d", len(foundation))
	}
	proxyBindings := 0
	for _, d := range foundation {
		if hasWildcard(d) {
			t.Fatal("wildcard in observability foundation")
		}
		switch d["kind"] {
		case "ServiceAccount":
			if (path(d, "metadata", "name") == "otel-collector" || path(d, "metadata", "name") == "grafana") && d["automountServiceAccountToken"] != false {
				t.Fatal("Collector must not have Kubernetes API credentials")
			}
			if path(d, "metadata", "namespace") != "observability" {
				t.Fatal("wrong SA namespace")
			}
		case "ClusterRole":
			if path(d, "metadata", "name") != "lab-prometheus-kind-proxy" || len(items(d["rules"])) != 2 {
				t.Fatal("unexpected cluster privilege")
			}
			b, _ := json.Marshal(d["rules"])
			if string(b) != `[{"apiGroups":[""],"resources":["nodes"],"verbs":["get","list","watch"]},{"apiGroups":[""],"resources":["nodes/proxy"],"verbs":["get"]}]` {
				t.Fatal("proxy role changed")
			}
		case "ClusterRoleBinding":
			proxyBindings++
			b, _ := json.Marshal(d["subjects"])
			if string(b) != `[{"kind":"ServiceAccount","name":"prometheus","namespace":"observability"}]` || path(d, "roleRef", "name") != "lab-prometheus-kind-proxy" {
				t.Fatal("proxy permission exposed to another SA")
			}
		case "Role":
			if path(d, "metadata", "namespace") != "account" || path(d, "metadata", "name") != "lab-ksm-account-read" {
				t.Fatal("KSM escaped account")
			}
			b, _ := json.Marshal(d["rules"])
			if string(b) != `[{"apiGroups":[""],"resources":["pods"],"verbs":["get","list","watch"]},{"apiGroups":["apps"],"resources":["deployments","replicasets","statefulsets"],"verbs":["get","list","watch"]}]` {
				t.Fatal("KSM has unnecessary access")
			}
		case "RoleBinding":
			b, _ := json.Marshal(d["subjects"])
			if path(d, "metadata", "namespace") != "account" || path(d, "roleRef", "name") != "lab-ksm-account-read" || string(b) != `[{"kind":"ServiceAccount","name":"kube-state-metrics","namespace":"observability"}]` {
				t.Fatal("wrong KSM binding")
			}
		default:
			t.Fatalf("unexpected foundation resource %v", d["kind"])
		}
	}
	if proxyBindings != 1 {
		t.Fatal("proxy privilege must have a single binding owner")
	}
	prom := docs(t, filepath.Join(dir, "prometheus.yaml"))
	deployments, pvcs, configs := 0, 0, 0
	var budget []map[string]any
	for _, d := range prom {
		switch d["kind"] {
		case "Deployment":
			deployments++
			pod := object(path(d, "spec", "template", "spec"))
			sa := pod["serviceAccountName"]
			if sa != "prometheus" && sa != "kube-state-metrics" {
				t.Fatal("unexpected exporter/SA")
			}
			if path(pod, "securityContext", "seccompProfile", "type") != "RuntimeDefault" || path(pod, "securityContext", "runAsNonRoot") != true {
				t.Fatal("unsafe Pod security")
			}
			for _, c := range items(pod["containers"]) {
				if image, _ := path(c, "image").(string); !regexp.MustCompile(`@sha256:[0-9a-f]{64}$`).MatchString(image) {
					t.Fatal("unpinned image")
				}
				for _, bound := range []string{"requests", "limits"} {
					for _, r := range []string{"cpu", "memory", "ephemeral-storage"} {
						if path(c, "resources", bound, r) == nil {
							t.Fatalf("missing %s %s", bound, r)
						}
					}
				}
				if path(c, "securityContext", "allowPrivilegeEscalation") != false {
					t.Fatal("privilege escalation allowed")
				}
				budget = append(budget, map[string]any{"workload": path(d, "metadata", "name"), "container": path(c, "name"), "image": path(c, "image"), "resources": path(c, "resources")})
				args, _ := json.Marshal(path(c, "args"))
				text := string(args)
				if sa == "kube-state-metrics" && (!strings.Contains(text, "--namespaces=account") || !strings.Contains(text, "--resources=pods,deployments,replicasets,statefulsets")) {
					t.Fatal("KSM scope changed")
				}
				if path(c, "name") == "prometheus-server" && (!strings.Contains(text, "--storage.tsdb.retention.time=3d") || !strings.Contains(text, "--storage.tsdb.retention.size=2GB")) {
					t.Fatal("retention changed")
				}
			}
		case "PersistentVolumeClaim":
			pvcs++
			if path(d, "spec", "resources", "requests", "storage") != "5Gi" || path(d, "metadata", "annotations", "argocd.argoproj.io/sync-options") != "Prune=confirm,Delete=confirm" {
				t.Fatal("PVC retention guard missing")
			}
		case "ConfigMap":
			configs++
			raw, _ := path(d, "data", "prometheus.yml").(string)
			var cfg map[string]any
			if yaml.Unmarshal([]byte(raw), &cfg) != nil || path(cfg, "global", "scrape_interval") != "30s" {
				t.Fatal("invalid scrape config")
			}
			jobs := items(cfg["scrape_configs"])
			if len(jobs) != 5 {
				t.Fatal("unexpected discovery job")
			}
			for _, j := range jobs {
				name := path(j, "job_name")
				if name != "prometheus" && name != "kube-state-metrics" && name != "kubernetes-cadvisor" && name != "otel-application" && name != "otel-collector-self" {
					t.Fatal("unapproved discovery job")
				}
				if name == "otel-application" && path(j, "honor_labels") != true {
					t.Fatal("scrape must preserve SDK job/instance labels")
				}
				if path(j, "job_name") == "kubernetes-cadvisor" {
					if path(j, "tls_config", "insecure_skip_verify") != false || path(j, "tls_config", "ca_file") != "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt" || path(j, "bearer_token_file") != "/var/run/secrets/kubernetes.io/serviceaccount/token" {
						t.Fatal("TLS/authentication weakened")
					}
					b, _ := json.Marshal(j)
					if !strings.Contains(string(b), "kubernetes.default.svc:443") || !strings.Contains(string(b), "/api/v1/nodes/$1/proxy/metrics/cadvisor") {
						t.Fatal("wrong kind scrape path")
					}
				}
			}
		case "Service":
			if path(d, "spec", "type") != "ClusterIP" {
				t.Fatal("metrics externally exposed")
			}
		default:
			t.Fatalf("chart generated forbidden/double-owned resource %v", d["kind"])
		}
	}
	if deployments != 2 || pvcs != 1 || configs != 1 || len(budget) != 3 {
		t.Fatal("extra or missing monitoring component")
	}
	// 基盤所有の全runtime containerとinit/helper profileを含める。Pod schedulingはmax(init要求量, app要求量合計)で、init Podを無条件加算しないようraw行にphase/replicasを残す。
	for _, file := range []string{"argocd-pods.yaml", "gitlab-runner.yaml"} {
		for _, d := range docs(t, filepath.Join(dir, file)) {
			kind := d["kind"]
			if kind != "Deployment" && kind != "StatefulSet" && kind != "Job" {
				continue
			}
			pod := object(path(d, "spec", "template", "spec"))
			for _, phase := range []string{"containers", "initContainers"} {
				for _, c := range items(pod[phase]) {
					for _, bound := range []string{"requests", "limits"} {
						for _, r := range []string{"cpu", "memory", "ephemeral-storage"} {
							if path(c, "resources", bound, r) == nil {
								t.Fatalf("%s/%v/%v missing %s %s", file, path(d, "metadata", "name"), path(c, "name"), bound, r)
							}
						}
					}
					if image, _ := path(c, "image").(string); !regexp.MustCompile(`@sha256:[0-9a-f]{64}$`).MatchString(image) {
						t.Fatalf("%s contains an unpinned runtime image", file)
					}
					budget = append(budget, map[string]any{"render": file, "workload": path(d, "metadata", "name"), "container": path(c, "name"), "phase": phase, "replicas": path(d, "spec", "replicas"), "image": path(c, "image"), "resources": path(c, "resources")})
				}
			}
		}
	}
	input, err := os.ReadFile(filepath.Join(dir, "prometheus.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(input)
	report, _ := json.MarshalIndent(map[string]any{"renderSHA256": hex.EncodeToString(hash[:]), "containers": budget}, "", "  ")
	if err = os.WriteFile(filepath.Join(dir, "observability-budget.json"), append(report, '\n'), 0644); err != nil {
		t.Fatal(err)
	}
	t.Log("single-owner RBAC, account-only KSM, TLS/proxy/auth, pinned bounded containers and protected PVC passed")
}
