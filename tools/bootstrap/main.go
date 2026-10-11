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
	Spec      struct {
		Source struct{ TargetRevision string }
	} `json:"spec"`
	Status struct {
		Sync      struct{ Status, Revision string } `json:"sync"`
		Health    struct{ Status string }           `json:"health"`
		Resources []struct {
			Kind, Name, Namespace, Status string
			Health                        struct{ Status string }
		} `json:"resources"`
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
	if op.Phase != "Succeeded" {
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

type leafExpectation struct {
	name, revision, hook       string
	resourceKind, resourceName string
}

var fullRevision = regexp.MustCompile(`^[0-9a-f]{40}$`)

func expectedLeaves(revision string, orderEnabled bool, orderRevision string) ([]leafExpectation, error) {
	if !fullRevision.MatchString(revision) {
		return nil, fmt.Errorf("Account revision must be a full lowercase Manifest commit SHA")
	}
	if (orderEnabled && !fullRevision.MatchString(orderRevision)) || (!orderEnabled && orderRevision != "") {
		return nil, fmt.Errorf("order-enabled requires a full order-revision; disabled Order requires no order-revision")
	}
	leaves := []leafExpectation{
		{name: "lab-local-postgresql", revision: revision},
		{name: "lab-local-backend", revision: revision, hook: "backend-migration"},
		{name: "lab-local-frontend", revision: revision},
	}
	if orderEnabled {
		leaves = append(leaves,
			leafExpectation{name: "lab-local-order-postgresql", revision: orderRevision, resourceKind: "StatefulSet", resourceName: "order-postgresql"},
			leafExpectation{name: "lab-local-order", revision: orderRevision, hook: "order-migration", resourceKind: "Deployment", resourceName: "order"})
	}
	return leaves, nil
}

func (leaf leafExpectation) ready(data []byte) error {
	if err := leafReady(data, leaf.revision, leaf.hook); err != nil {
		return err
	}
	if leaf.resourceKind == "" {
		return nil
	}
	var a app
	if err := json.Unmarshal(data, &a); err != nil {
		return err
	}
	if a.Spec.Source.TargetRevision != leaf.revision || a.Status.OperationState.SyncResult.Revision != leaf.revision {
		return fmt.Errorf("Order source/operation revision does not match the fixed Manifest commit")
	}
	// Require the observed child workload too; a parent's health or wave alone is insufficient.
	for _, r := range a.Status.Resources {
		if r.Kind == leaf.resourceKind && r.Name == leaf.resourceName && r.Namespace == "account" && r.Status == "Synced" && r.Health.Status == "Healthy" {
			return nil
		}
	}
	return fmt.Errorf("Synced/Healthy %s %s observation missing", leaf.resourceKind, leaf.resourceName)
}

// 全体timeoutと1回読取10秒（kubectl request8秒）を重ねる。前段leafも毎周回再確認し、途中の劣化を見逃さない。
func main() {
	contextName := flag.String("context", "", "explicit kubectl context (required)")
	revision := flag.String("revision", "", "application-manifest full commit SHA (required)")
	orderEnabled := flag.Bool("order-enabled", false, "observe the two registered Order leaves (must match workloads values)")
	orderRevision := flag.String("order-revision", "", "fixed Order Manifest commit SHA (required only with order-enabled)")
	kubectl := flag.String("kubectl", "kubectl", "kubectl executable")
	timeout := flag.Duration("timeout", 10*time.Minute, "total timeout")
	flag.Parse()
	leaves, err := expectedLeaves(*revision, *orderEnabled, *orderRevision)
	if *contextName == "" || err != nil || *timeout <= 0 || flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "explicit context, full revision, valid Order options and positive timeout required; no positional arguments")
		os.Exit(2)
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()
	// 毎回前段leafも再確認する。後段がReadyでも前段が不健康/別revisionへ変わった状態を見逃さない。
	for {
		ready := true
		for _, leaf := range leaves {
			readCtx, readCancel := context.WithTimeout(ctx, 10*time.Second)
			data, err := exec.CommandContext(readCtx, *kubectl, "--context", *contextName, "--request-timeout=8s", "-n", "argocd", "get", "application", leaf.name, "-o", "json").Output()
			readCancel()
			if err == nil {
				err = leaf.ready(data)
			}
			if err != nil {
				fmt.Printf("%s %s WAIT %v\n", time.Now().UTC().Format(time.RFC3339), leaf.name, err)
				ready = false
				break
			}
			fmt.Printf("%s %s READY revision=%s hook=%s\n", time.Now().UTC().Format(time.RFC3339), leaf.name, leaf.revision, leaf.hook)
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
