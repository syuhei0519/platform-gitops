// 保存形状のApplication JSONでleaf revision/health/sync/hook判定を確認する。
// 親Healthyだけでmigration成功と誤判定する退行を防ぎ、実clusterへ照会しない。
package main

import (
	"encoding/json"
	"os"
	"testing"

	lua "github.com/yuin/gopher-lua"
	"gopkg.in/yaml.v3"
)

func luaValue(l *lua.LState, v any) lua.LValue {
	switch x := v.(type) {
	case map[string]any:
		table := l.NewTable()
		for k, value := range x {
			table.RawSetString(k, luaValue(l, value))
		}
		return table
	case string:
		return lua.LString(x)
	case nil:
		return lua.LNil
	default:
		panic("unsupported fixture type")
	}
}

func TestApplicationHealth(t *testing.T) {
	data, err := os.ReadFile("../../bootstrap/argocd-values.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var values struct {
		Configs struct {
			CM map[string]string `yaml:"cm"`
		} `yaml:"configs"`
	}
	if err := yaml.Unmarshal(data, &values); err != nil {
		t.Fatal(err)
	}
	script := values.Configs.CM["resource.customizations.health.argoproj.io_Application"]
	if script == "" {
		t.Fatal("health override missing")
	}
	cases := []struct{ name, input, want string }{
		{"no-status", `{}`, "Progressing"},
		{"missing-health", `{"status":{"sync":{"status":"Synced"}}}`, "Progressing"},
		{"missing-sync", `{"status":{"health":{"status":"Healthy"}}}`, "Progressing"},
		{"out-of-sync", `{"status":{"sync":{"status":"OutOfSync"},"health":{"status":"Healthy"}}}`, "Progressing"},
		{"degraded-out-of-sync", `{"status":{"sync":{"status":"OutOfSync"},"health":{"status":"Degraded"}}}`, "Degraded"},
		{"progressing", `{"status":{"sync":{"status":"Synced"},"health":{"status":"Progressing"}}}`, "Progressing"},
		{"healthy", `{"status":{"sync":{"status":"Synced"},"health":{"status":"Healthy"}}}`, "Healthy"},
		{"failed-sync", `{"status":{"sync":{"status":"Synced"},"health":{"status":"Healthy"},"operationState":{"phase":"Failed"}}}`, "Degraded"},
		{"running-sync", `{"status":{"sync":{"status":"Synced"},"health":{"status":"Healthy"},"operationState":{"phase":"Running"}}}`, "Progressing"},
		{"pending-operation", `{"operation":{},"status":{"sync":{"status":"Synced"},"health":{"status":"Healthy"}}}`, "Progressing"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			l := lua.NewState(lua.Options{SkipOpenLibs: true})
			defer l.Close()
			var obj map[string]any
			if err := json.Unmarshal([]byte(tc.input), &obj); err != nil {
				t.Fatal(err)
			}
			l.SetGlobal("obj", luaValue(l, obj))
			if err := l.DoString(script); err != nil {
				t.Fatal(err)
			}
			if got := l.Get(-1).(*lua.LTable).RawGetString("status").String(); got != tc.want {
				t.Fatalf("got %s want %s", got, tc.want)
			}
		})
	}
}

func TestLeafRevisionAndMigration(t *testing.T) {
	const revision = "194565e637331e9bcec970809d4853ff5ad59e97"
	good := `{"status":{"sync":{"status":"Synced","revision":"` + revision + `"},"health":{"status":"Healthy"},"operationState":{"phase":"Succeeded","syncResult":{"revision":"` + revision + `","resources":[{"kind":"Job","name":"backend-migration","namespace":"account","hookType":"Sync","hookPhase":"Succeeded"}]}}}}`
	var obj map[string]any
	_ = json.Unmarshal([]byte(good), &obj)
	if err := leafReady([]byte(good), revision, "backend-migration"); err != nil {
		t.Fatal(err)
	}
	if err := leafReady([]byte(good), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ""); err == nil {
		t.Fatal("accepted old revision")
	}
	for _, phase := range []string{"Running", "Terminating", "Failed", "Error"} {
		obj["status"].(map[string]any)["operationState"].(map[string]any)["phase"] = phase
		data, _ := json.Marshal(obj)
		if leafReady(data, revision, "") == nil {
			t.Fatalf("accepted %s", phase)
		}
	}
	_ = json.Unmarshal([]byte(good), &obj)
	obj["status"].(map[string]any)["operationState"].(map[string]any)["syncResult"].(map[string]any)["revision"] = "old"
	data, _ := json.Marshal(obj)
	if leafReady(data, revision, "backend-migration") == nil {
		t.Fatal("accepted stale migration")
	}
	if leafReady(data, revision, "") != nil {
		t.Fatal("non-hook leaf should accept matching comparison revision")
	}
	if leafReady([]byte(`{}`), revision, "") == nil {
		t.Fatal("accepted missing status")
	}
	if leafReady([]byte(`not-json`), revision, "") == nil {
		t.Fatal("accepted invalid JSON")
	}
	_ = json.Unmarshal([]byte(good), &obj)
	obj["status"].(map[string]any)["operationState"].(map[string]any)["syncResult"].(map[string]any)["resources"] = []any{}
	data, _ = json.Marshal(obj)
	if leafReady(data, revision, "backend-migration") == nil {
		t.Fatal("accepted missing migration hook")
	}
}
