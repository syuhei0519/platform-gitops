// renderされたCollector構成のResource/queue/retry/port/単一Pod境界を確認する。
// 構成の検証と実送信障害時の容量・span保存率測定は別の確認。
package main

import (
	"gopkg.in/yaml.v3"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestCollectorContract(t *testing.T) {
	dir := os.Getenv("PE_OBSERVABILITY_RENDER_DIR")
	if dir == "" {
		t.Skip("rendered deployment/config required; fixed-binary/runtime acceptance is separate")
	}
	for _, mode := range []string{"disabled", "enabled"} {
		var deployment, config, service map[string]any
		for _, d := range docs(t, filepath.Join(dir, "collector-"+mode+".yaml")) {
			switch d["kind"] {
			case "Deployment":
				deployment = d
			case "ConfigMap":
				config = d
			case "Service":
				service = d
			case "Secret":
				t.Fatal("Secret value must never be rendered")
			}
		}
		if path(deployment, "spec", "replicas") != 1 || path(deployment, "spec", "strategy", "type") != "Recreate" {
			t.Fatal("maximum-one Ready requires replicas=1/Recreate")
		}
		if path(deployment, "spec", "template", "spec", "automountServiceAccountToken") != false {
			t.Fatal("Collector has no Kubernetes API credentials")
		}
		pod := object(path(deployment, "spec", "template", "spec"))
		c := object(items(pod["containers"])[0])
		if path(c, "resources", "limits", "memory") != "256Mi" {
			t.Fatal("fixed memory budget")
		}
		if path(service, "spec", "type") != "ClusterIP" {
			t.Fatal("OTLP must remain private")
		}
		raw := path(config, "data", "config.yaml").(string)
		var cfg map[string]any
		if err := yaml.Unmarshal([]byte(raw), &cfg); err != nil {
			t.Fatal(err)
		}
		protocols := object(path(cfg, "receivers", "otlp", "protocols"))
		if len(protocols) != 1 || path(protocols, "http", "endpoint") != "0.0.0.0:4318" {
			t.Fatal("HTTP-only OTLP")
		}
		attrs := items(path(cfg, "processors", "resource/defaults", "attributes"))
		for _, v := range attrs {
			a := object(v)
			if a["action"] != "insert" || (a["key"] != "deployment.environment.name" && a["key"] != "k8s.namespace.name") {
				t.Fatal("must preserve source identity and only fill missing defaults")
			}
		}
		if path(cfg, "exporters", "prometheus", "resource_to_telemetry_conversion", "enabled") != false {
			t.Fatal("do not copy source SHA to every histogram series")
		}
		p := object(path(cfg, "service", "pipelines"))
		want := []any{"memory_limiter", "filter/resource_contract", "resource/defaults", "batch"}
		for _, signal := range []string{"metrics", "traces"} {
			if !reflect.DeepEqual(path(p, signal, "processors"), want) {
				t.Fatal("bounded pipeline order")
			}
		}
		if mode == "enabled" {
			if path(cfg, "exporters", "otlphttp/newrelic", "headers", "api-key") != "${env:NR_LICENSE_KEY}" || !strings.Contains(raw, "insecure_skip_verify: false") {
				t.Fatal("Secret env and TLS required")
			}
			env := items(c["env"])
			if len(env) != 1 || path(env[0], "valueFrom", "secretKeyRef", "name") != "newrelic-otlp" || path(env[0], "valueFrom", "secretKeyRef", "key") != "license-key" {
				t.Fatal("only Collector consumes ingest Secret")
			}
			if path(cfg, "exporters", "otlphttp/newrelic", "retry_on_failure", "max_elapsed_time") != "30s" || path(cfg, "exporters", "otlphttp/newrelic", "sending_queue", "queue_size") != 32 {
				t.Fatal("retry and queue must be finite")
			}
		} else {
			if len(items(c["env"])) != 0 || path(cfg, "exporters", "otlphttp/newrelic") != nil {
				t.Fatal("disabled path must not access credential or send externally")
			}
		}
	}
}
