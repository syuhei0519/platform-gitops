// Order の配備定義と既存 Account の境界を、入力ごとのテストで確認します。
// Synthetic observations and fake kubectl only; no cluster operations.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

const accountFixtureSHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
const orderFixtureSHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

func TestExpectedLeaves(t *testing.T) {
	disabled, err := expectedLeaves(accountFixtureSHA, false, "")
	if err != nil || len(disabled) != 3 {
		t.Fatal(disabled, err)
	}
	enabled, err := expectedLeaves(accountFixtureSHA, true, orderFixtureSHA)
	if err != nil || len(enabled) != 5 || !reflect.DeepEqual(enabled[:3], disabled) {
		t.Fatal(enabled, err)
	}
	for i, name := range []string{"lab-local-postgresql", "lab-local-backend", "lab-local-frontend", "lab-local-order-postgresql", "lab-local-order"} {
		want := accountFixtureSHA
		if i >= 3 {
			want = orderFixtureSHA
		}
		if enabled[i].name != name || enabled[i].revision != want {
			t.Fatal("mixed identity/revision", enabled)
		}
	}
	if enabled[1].hook != "backend-migration" || enabled[4].hook != "order-migration" {
		t.Fatal("mixed hooks")
	}
	for _, r := range []string{"", "main", "HEAD", "123", strings.Repeat("a", 39), strings.Repeat("a", 41), strings.Repeat("A", 40), strings.Repeat("g", 40), " " + orderFixtureSHA} {
		if _, err := expectedLeaves(accountFixtureSHA, true, r); err == nil {
			t.Fatalf("accepted Order %q", r)
		}
		if _, err := expectedLeaves(r, false, ""); err == nil {
			t.Fatalf("accepted Account %q", r)
		}
	}
	if _, err := expectedLeaves(accountFixtureSHA, false, orderFixtureSHA); err == nil {
		t.Fatal("ignored disabled revision")
	}
}
func observation(leaf leafExpectation) map[string]any {
	hooks := []any{}
	if leaf.hook != "" {
		hooks = append(hooks, map[string]any{"kind": "Job", "name": leaf.hook, "namespace": "account", "hookType": "Sync", "hookPhase": "Succeeded"})
	}
	source := func() map[string]any {
		return map[string]any{"repoURL": "https://github.com/syuhei0519/application-manifest.git", "targetRevision": leaf.revision, "path": "charts/" + leaf.resourceName, "helm": map[string]any{"releaseName": leaf.resourceName, "valueFiles": []any{"../../environments/local/" + leaf.resourceName + ".yaml"}}}
	}
	destination := func() map[string]any {
		return map[string]any{"server": "https://kubernetes.default.svc", "namespace": "account"}
	}
	return map[string]any{"metadata": map[string]any{"name": leaf.name, "namespace": "argocd"}, "spec": map[string]any{"project": "lab-account", "source": source(), "destination": destination()}, "status": map[string]any{
		"sync": map[string]any{"status": "Synced", "revision": leaf.revision, "comparedTo": map[string]any{"source": source(), "destination": destination()}}, "health": map[string]any{"status": "Healthy"},
		"operationState": map[string]any{"phase": "Succeeded", "syncResult": map[string]any{"revision": leaf.revision, "source": source(), "resources": hooks}},
		"resources":      []any{map[string]any{"kind": leaf.resourceKind, "name": leaf.resourceName, "namespace": "account", "status": "Synced", "health": map[string]any{"status": "Healthy"}}}}}
}
func change(obj map[string]any, path string, value any) {
	parts := strings.Split(path, ".")
	for _, p := range parts[:len(parts)-1] {
		obj = obj[p].(map[string]any)
	}
	obj[parts[len(parts)-1]] = value
}
func TestOrderLeafBoundaries(t *testing.T) {
	leaves, _ := expectedLeaves(accountFixtureSHA, true, orderFixtureSHA)
	for _, leaf := range leaves[3:] {
		t.Run(leaf.name, func(t *testing.T) {
			data, _ := json.Marshal(observation(leaf))
			if err := leaf.ready(data); err != nil {
				t.Fatal(err)
			}
			cases := []struct {
				path   string
				values []any
			}{
				{"operation", []any{map[string]any{}}},
				{"spec.source.targetRevision", []any{"", "main", accountFixtureSHA}},
				{"status.sync.status", []any{"", "OutOfSync", "Unknown"}},
				{"status.sync.revision", []any{"", accountFixtureSHA}},
				{"status.health.status", []any{"", "Missing", "Progressing", "Degraded", "Unknown", "Suspended"}},
				{"status.operationState.phase", []any{"", "Running", "Terminating", "Failed", "Error", "FuturePhase"}},
				{"status.operationState.syncResult.revision", []any{"", accountFixtureSHA}},
				{"status.resources", []any{nil, []any{}}},
			}
			for _, tc := range cases {
				for _, v := range tc.values {
					obj := observation(leaf)
					change(obj, tc.path, v)
					data, _ := json.Marshal(obj)
					if leaf.ready(data) == nil {
						t.Fatalf("accepted %s=%v", tc.path, v)
					}
				}
			}
			for _, field := range []string{"kind", "name", "namespace", "status", "health"} {
				obj := observation(leaf)
				r := obj["status"].(map[string]any)["resources"].([]any)[0].(map[string]any)
				if field == "health" {
					r[field] = map[string]any{"status": "Progressing"}
				} else {
					r[field] = "wrong"
				}
				data, _ := json.Marshal(obj)
				if leaf.ready(data) == nil {
					t.Fatalf("accepted wrong resource %s", field)
				}
			}
			for _, raw := range []string{"null", "{}", "{", `{"status":{}}`} {
				if leaf.ready([]byte(raw)) == nil {
					t.Fatal("accepted missing observation")
				}
			}
		})
	}
	order := leaves[4]
	for _, field := range []string{"kind", "name", "namespace", "hookType", "hookPhase"} {
		for _, v := range []string{"", "backend-migration", "Running", "Failed", "Error"} {
			obj := observation(order)
			hook := obj["status"].(map[string]any)["operationState"].(map[string]any)["syncResult"].(map[string]any)["resources"].([]any)[0].(map[string]any)
			hook[field] = v
			data, _ := json.Marshal(obj)
			if order.ready(data) == nil {
				t.Fatalf("accepted hook %s=%s", field, v)
			}
		}
	}
	obj := observation(order)
	change(obj, "status.operationState.syncResult.resources", []any{})
	data, _ := json.Marshal(obj)
	if order.ready(data) == nil {
		t.Fatal("accepted missing migration")
	}
	for _, leaf := range leaves[:3] {
		for _, phase := range []string{"", "FuturePhase"} {
			obj := observation(leaf)
			change(obj, "status.operationState.phase", phase)
			data, _ := json.Marshal(obj)
			if leaf.ready(data) == nil {
				t.Fatal("accepted unknown legacy operation")
			}
		}
	}
}
func TestObserverProcess(t *testing.T) {
	if os.Getenv("ORDER_OBSERVER_TEST_PROCESS") != "1" {
		return
	}
	for i, arg := range os.Args {
		if arg == "--" {
			os.Args = append([]string{os.Args[0]}, os.Args[i+1:]...)
			break
		}
	}
	flag.CommandLine = flag.NewFlagSet(os.Args[0], flag.ExitOnError)
	main()
	os.Exit(0)
}
func TestObserverCLI(t *testing.T) {
	leaves, _ := expectedLeaves(accountFixtureSHA, true, orderFixtureSHA)
	for _, scenario := range []string{"disabled", "enabled", "missing-db", "missing-order", "failed-hook", "progressing-db", "missing-revision", "wrong-source", "invalid-options", "regressed-db", "deleting-db", "deleting-order", "deleting-account", "stale-comparison", "stale-operation"} {
		t.Run(scenario, func(t *testing.T) {
			dir := t.TempDir()
			for _, leaf := range leaves {
				obj := observation(leaf)
				if scenario == "missing-db" && leaf.name == leaves[3].name || scenario == "missing-order" && leaf.name == leaves[4].name {
					continue
				}
				if scenario == "progressing-db" && leaf.name == leaves[3].name {
					change(obj, "status.health.status", "Progressing")
				}
				if scenario == "failed-hook" && leaf.name == leaves[4].name {
					change(obj, "status.operationState.syncResult.resources", []any{})
				}
				if scenario == "missing-revision" && leaf.name == leaves[4].name {
					change(obj, "status.sync.revision", "")
				}
				if scenario == "wrong-source" && leaf.name == leaves[3].name {
					change(obj, "spec.source.targetRevision", "main")
				}
				if scenario == "deleting-db" && leaf.name == leaves[3].name || scenario == "deleting-order" && leaf.name == leaves[4].name || scenario == "deleting-account" && leaf.name == leaves[0].name {
					change(obj, "metadata.deletionTimestamp", "2026-10-11T00:00:00Z")
					change(obj, "metadata.finalizers", []any{"resources-finalizer.argocd.argoproj.io"})
				}
				if scenario == "stale-comparison" && leaf.name == leaves[3].name {
					change(obj, "status.sync.comparedTo.source.helm.parameters", []any{map[string]any{"name": "enabled", "value": "false"}})
				}
				if scenario == "stale-operation" && leaf.name == leaves[4].name {
					change(obj, "status.operationState.syncResult.source.helm.values", "enabled: false")
				}
				data, _ := json.Marshal(obj)
				if err := os.WriteFile(filepath.Join(dir, leaf.name), data, 0600); err != nil {
					t.Fatal(err)
				}
			}
			script := "#!/bin/sh\nset -eu\necho \"$8\" >> \"$OBSERVER_FIXTURES/calls\"\n"
			if scenario == "regressed-db" {
				script += "if [ \"$8\" = lab-local-order ]; then printf '{}' > \"$OBSERVER_FIXTURES/lab-local-order-postgresql\"; if [ ! -f \"$OBSERVER_FIXTURES/once\" ]; then touch \"$OBSERVER_FIXTURES/once\"; exit 1; fi; fi\n"
			}
			script += "cat \"$OBSERVER_FIXTURES/$8\"\n"
			fake := filepath.Join(dir, "kubectl")
			if err := os.WriteFile(fake, []byte(script), 0700); err != nil {
				t.Fatal(err)
			}
			timeout := "700ms"
			if scenario == "disabled" || scenario == "enabled" {
				timeout = "5s"
			}
			if scenario == "regressed-db" {
				timeout = "2400ms"
			}
			args := []string{"-test.run=^TestObserverProcess$", "--", "-context", "synthetic-unused", "-revision", accountFixtureSHA, "-kubectl", fake, "-timeout", timeout}
			if scenario != "disabled" {
				args = append(args, "-order-enabled", "-order-revision", orderFixtureSHA)
			}
			if scenario == "invalid-options" {
				args = append(args, "-order-revision", "main")
			}
			processCtx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
			defer cancel()
			started := time.Now()
			cmd := exec.CommandContext(processCtx, os.Args[0], args...)
			cmd.Env = append(os.Environ(), "ORDER_OBSERVER_TEST_PROCESS=1", "OBSERVER_FIXTURES="+dir)
			output, err := cmd.CombinedOutput()
			if processCtx.Err() != nil {
				t.Fatalf("observer did not respect finite timeout: %s", output)
			}
			if strings.HasPrefix(scenario, "deleting-") && time.Since(started) > 4*time.Second {
				t.Fatalf("deleting Application exceeded finite wait: %s", output)
			}
			success := scenario == "enabled" || scenario == "disabled"
			if success && err != nil || !success && err == nil {
				t.Fatalf("unexpected result %v: %s", err, output)
			}
			if !success {
				exit, ok := err.(*exec.ExitError)
				want := 1
				if scenario == "invalid-options" {
					want = 2
				}
				if !ok || exit.ExitCode() != want {
					t.Fatalf("exit status %v, want %d", err, want)
				}
			}
			calls, _ := os.ReadFile(filepath.Join(dir, "calls"))
			if scenario == "invalid-options" {
				if len(calls) != 0 {
					t.Fatal("read before validation")
				}
				return
			}
			if scenario == "disabled" && strings.Contains(string(calls), "order") {
				t.Fatal("disabled Order queried")
			}
			if scenario == "enabled" && len(strings.Fields(string(calls))) != 5 {
				t.Fatalf("not all leaves read: %s", calls)
			}
			if scenario == "regressed-db" && strings.Count(string(calls), "lab-local-order-postgresql") < 2 {
				t.Fatal("earlier DB not rechecked")
			}
		})
	}
}
