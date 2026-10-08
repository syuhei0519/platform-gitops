package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"
)

func main() {
	policy := flag.String("policy", "", "fixed policy JSON")
	db := flag.String("db", "", "Trivy DB metadata")
	report := flag.String("report", "", "private raw Trivy JSON")
	out := flag.String("out", "", "safe summary JSON only")
	exit := flag.Int("scanner-exit", -1, "actual scanner exit code; mandatory")
	rendered := flag.String("rendered", "", "required IaC render directory for deployment repositories")
	renderOnly := flag.String("check-render-only", "", "structural guard before render artifact upload")
	flag.Parse()
	if *renderOnly != "" {
		f, e := inspectRendered(*renderOnly)
		if e != nil {
			fmt.Fprintln(os.Stderr, e.Error())
			os.Exit(2)
		}
		public := struct {
			Check    string    `json:"check"`
			Pass     bool      `json:"pass"`
			Findings []Finding `json:"findings"`
		}{"render-structural-boundary", len(f) == 0, f}
		b, e := json.MarshalIndent(public, "", "  ")
		if e != nil {
			fmt.Fprintln(os.Stderr, "safe render report failed")
			os.Exit(2)
		}
		if e = os.WriteFile(*out, append(b, '\n'), 0600); e != nil {
			fmt.Fprintln(os.Stderr, "safe render report write failed")
			os.Exit(2)
		}
		if !public.Pass {
			fmt.Fprintln(os.Stderr, "unsafe rendered data rejected before artifact upload")
			os.Exit(1)
		}
		fmt.Println("rendered structural boundary passed")
		return
	}
	if *exit != 0 {
		fmt.Fprintln(os.Stderr, "scanner did not complete successfully")
		os.Exit(2)
	}
	read := func(name string) []byte {
		b, e := os.ReadFile(name)
		if e != nil {
			fmt.Fprintln(os.Stderr, "required scanner input unavailable")
			os.Exit(2)
		}
		return b
	}
	p := read(*policy)
	s, err := evaluate(p, read(*db), read(*report), time.Now().UTC())
	if err != nil {
		fmt.Fprintln(os.Stderr, err.Error())
		os.Exit(2)
	}
	if *rendered != "" {
		f, e := inspectRendered(*rendered)
		if e != nil {
			fmt.Fprintln(os.Stderr, e.Error())
			os.Exit(2)
		}
		if len(f) > 0 {
			s.Pass = false
			s.Findings = append(s.Findings, f...)
		}
	}
	h := sha256.Sum256(p)
	public := struct {
		Summary
		PolicySHA256 string `json:"policySHA256"`
	}{s, hex.EncodeToString(h[:])}
	b, err := json.MarshalIndent(public, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "safe report generation failed")
		os.Exit(2)
	}
	if err = os.WriteFile(*out, append(b, '\n'), 0600); err != nil {
		fmt.Fprintln(os.Stderr, "safe report write failed")
		os.Exit(2)
	}
	if !s.Pass {
		fmt.Fprintln(os.Stderr, "security policy rejected findings; see safe summary")
		os.Exit(1)
	}
	fmt.Println("fixed scanner policy passed; warnings preserved in safe summary")
}
