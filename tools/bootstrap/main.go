// 初回leaf準備状態の読取専用CLI。明示context/revisionでApplication JSONを取得する。
// DB→backend migration→frontendの状態を再確認し、成功0・待機期限1・引数不正2で終了する。Secret取得/apply/syncは行わない。
// 初回leafの読取専用待機。Sync/apply/Secret取得は行わない。
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"time"
)

type app struct {
	Operation json.RawMessage `json:"operation"`
	Status    struct {
		Sync           struct{ Status, Revision string } `json:"sync"`
		Health         struct{ Status string }           `json:"health"`
		OperationState struct {
			Phase      string
			SyncResult struct {
				Revision  string
				Resources []struct{ Kind, Name, Namespace, HookType, HookPhase string }
			}
		} `json:"operationState"`
	} `json:"status"`
}

// Healthy/Syncedだけでなく対象revisionとoperation終端を照合。backendは同revisionのSync hook成功も必須。
func leafReady(data []byte, revision, hook string) error {
	var a app
	if err := json.Unmarshal(data, &a); err != nil {
		return fmt.Errorf("invalid Application JSON: %w", err)
	}
	if len(a.Operation) > 0 && string(a.Operation) != "null" {
		return fmt.Errorf("sync operation pending")
	}
	if a.Status.Sync.Status != "Synced" || a.Status.Health.Status != "Healthy" {
		return fmt.Errorf("sync=%s health=%s", a.Status.Sync.Status, a.Status.Health.Status)
	}
	if a.Status.Sync.Revision != revision {
		return fmt.Errorf("observed revision=%s expected=%s", a.Status.Sync.Revision, revision)
	}
	op := a.Status.OperationState
	if op.Phase == "Running" || op.Phase == "Terminating" || op.Phase == "Failed" || op.Phase == "Error" {
		return fmt.Errorf("operation phase=%s", op.Phase)
	}
	if hook != "" {
		if op.Phase != "Succeeded" || op.SyncResult.Revision != revision {
			return fmt.Errorf("migration sync evidence is not from expected revision; explicit whole-Application sync required")
		}
		for _, r := range op.SyncResult.Resources {
			if r.Kind == "Job" && r.Name == hook && r.Namespace == "account" && r.HookType == "Sync" && r.HookPhase == "Succeeded" {
				return nil
			}
		}
		return fmt.Errorf("successful Sync hook %s missing", hook)
	}
	return nil
}

// 全体timeoutと1回読取10秒（kubectl request8秒）を重ねる。前段leafも毎周回再確認し、途中の劣化を見逃さない。
func main() {
	contextName := flag.String("context", "", "explicit kubectl context (required)")
	revision := flag.String("revision", "", "application-manifest full commit SHA (required)")
	kubectl := flag.String("kubectl", "kubectl", "kubectl executable")
	timeout := flag.Duration("timeout", 10*time.Minute, "total timeout")
	flag.Parse()
	if *contextName == "" || !regexp.MustCompile(`^[0-9a-f]{40}$`).MatchString(*revision) || *timeout <= 0 {
		fmt.Fprintln(os.Stderr, "explicit context, full revision and positive timeout required")
		os.Exit(2)
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	leaves := []struct{ name, hook string }{{"lab-local-postgresql", ""}, {"lab-local-backend", "backend-migration"}, {"lab-local-frontend", ""}}
	// 毎回前段leafも再確認する。後段がReadyでも前段が不健康/別revisionへ変わった状態を見逃さない。
	for {
		ready := true
		for _, leaf := range leaves {
			readCtx, readCancel := context.WithTimeout(ctx, 10*time.Second)
			data, err := exec.CommandContext(readCtx, *kubectl, "--context", *contextName, "--request-timeout=8s", "-n", "argocd", "get", "application", leaf.name, "-o", "json").Output()
			readCancel()
			if err == nil {
				err = leafReady(data, *revision, leaf.hook)
			}
			if err != nil {
				fmt.Printf("%s WAIT %v\n", time.Now().UTC().Format(time.RFC3339), err)
				ready = false
				break
			}
			fmt.Printf("%s %s READY revision=%s hook=%s\n", time.Now().UTC().Format(time.RFC3339), leaf.name, *revision, leaf.hook)
		}
		if ready {
			return
		}
		select {
		case <-ctx.Done():
			fmt.Fprintln(os.Stderr, "leaf readiness timeout; preserve failed Job diagnostics and perform explicit whole-Application sync after repair")
			os.Exit(1)
		case <-time.After(2 * time.Second):
		}
	}
}
