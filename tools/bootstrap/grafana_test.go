package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGrafanaProvisioningBoundary(t *testing.T) {
	dir := os.Getenv("PE_OBSERVABILITY_RENDER_DIR")
	if dir == "" {
		t.Skip("fixed rendered Grafana required")
	}
	var deployment, pvc, config, dashboards map[string]any
	for _, d := range docs(t, filepath.Join(dir, "grafana.yaml")) {
		switch d["kind"] {
		case "Deployment":
			deployment = d
		case "PersistentVolumeClaim":
			pvc = d
		case "ConfigMap":
			if path(d, "metadata", "name") == "grafana-dashboards" {
				dashboards = d
			} else {
				config = d
			}
		case "Service":
			if path(d, "spec", "type") != "ClusterIP" {
				t.Fatal("external Grafana exposure")
			}
		default:
			t.Fatalf("unexpected Grafana-owned kind %v", d["kind"])
		}
	}
	if path(deployment, "spec", "strategy", "type") != "Recreate" || path(deployment, "spec", "replicas") != 1 {
		t.Fatal("Grafana single Recreate replica")
	}
	spec := path(deployment, "spec", "template", "spec")
	if path(spec, "automountServiceAccountToken") != false || path(spec, "serviceAccountName") != "grafana" || path(spec, "securityContext", "runAsNonRoot") != true {
		t.Fatal("Grafana pod access boundary")
	}
	if len(items(path(spec, "initContainers"))) != 0 || len(items(path(spec, "containers"))) != 1 {
		t.Fatal("no root init, download job or sidecars")
	}
	c := items(path(spec, "containers"))[0]
	volumes := map[string]bool{}
	for _, v := range items(path(spec, "volumes")) {
		name, _ := path(v, "name").(string)
		if name == "" || volumes[name] {
			t.Fatal("invalid/duplicate Grafana volume name")
		}
		volumes[name] = true
	}
	mounts := map[string]bool{}
	for _, v := range items(path(c, "volumeMounts")) {
		mount, _ := path(v, "mountPath").(string)
		if mount == "" || mounts[mount] {
			t.Fatal("invalid/duplicate Grafana mountPath")
		}
		mounts[mount] = true
	}
	if path(c, "image") != "docker.io/grafana/grafana:13.2.3@sha256:d84563330dc9d2fd2bc096d0fb96021b5319c75bf6ed555566709998e823dec4" {
		t.Fatal("fixed Grafana image")
	}
	if path(c, "securityContext", "readOnlyRootFilesystem") != true || path(c, "securityContext", "allowPrivilegeEscalation") != false {
		t.Fatal("Grafana container boundary")
	}
	if path(c, "resources", "requests", "memory") != "128Mi" || path(c, "resources", "limits", "memory") != "512Mi" {
		t.Fatal("Grafana resource budget")
	}
	for _, e := range items(path(c, "env")) {
		if strings.HasPrefix(path(e, "name").(string), "GF_SECURITY_ADMIN_") {
			if path(e, "value") != nil || path(e, "valueFrom", "secretKeyRef", "name") != "grafana-admin" {
				t.Fatal("admin secret must be operator-owned SecretKeyRef")
			}
		}
	}
	if path(pvc, "spec", "resources", "requests", "storage") != "1Gi" || path(pvc, "metadata", "annotations", "helm.sh/resource-policy") != "keep" || !strings.Contains(path(pvc, "metadata", "annotations", "argocd.argoproj.io/sync-options").(string), "Delete=confirm") {
		t.Fatal("Grafana PVC protection")
	}
	data := object(path(config, "data"))
	if !strings.Contains(data["datasources.yaml"].(string), "uid: prometheus") || !strings.Contains(data["datasources.yaml"].(string), "http://prometheus-server.observability.svc.cluster.local") || !strings.Contains(data["dashboardproviders.yaml"].(string), "editable: false") {
		t.Fatal("Git-owned provisioning")
	}
	for _, name := range []string{"platform-overview.json", "application-overview.json"} {
		a, err := os.ReadFile(filepath.Join(filepath.Dir(dir), "observability", "dashboards", name))
		if err != nil {
			t.Fatal(err)
		}
		b, err := os.ReadFile(filepath.Join(filepath.Dir(dir), "charts", "grafana", "files", name))
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(a, b) {
			t.Fatal("canonical/chart dashboard drift")
		}
		var body map[string]any
		if json.Unmarshal([]byte(object(path(dashboards, "data"))[name].(string)), &body) != nil || body["editable"] != false {
			t.Fatal("invalid provisioned dashboard")
		}
		if len(items(body["panels"])) < 10 {
			t.Fatal("missing frozen indicators")
		}
	}
}
