package main

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

var at = time.Date(2026, 10, 2, 20, 0, 0, 0, time.UTC)
var policy = []byte(`{"schemaVersion":1,"trivyVersion":"0.75.0","dbMaxAgeHours":24,"exceptions":[]}`)
var metadata = []byte(`{"Version":2,"UpdatedAt":"2026-10-02T12:48:00Z","DownloadedAt":"2026-10-02T19:59:00Z"}`)

func report(results string) []byte {
	return []byte(`{"SchemaVersion":2,"Trivy":{"Version":"0.75.0"},"ArtifactType":"filesystem","ArtifactName":"/source","Results":[` + results + `]}`)
}
func TestFixedSeverityDecisions(t *testing.T) {
	for _, c := range []struct {
		severity, fix string
		pass          bool
	}{{"CRITICAL", "2.0.0", false}, {"CRITICAL", "", true}, {"HIGH", "2.0.0", true}, {"LOW", "2.0.0", true}} {
		r := report(`{"Target":"package-lock.json","Vulnerabilities":[{"VulnerabilityID":"CVE-2026-12345","PkgName":"demo","InstalledVersion":"1.0.0","FixedVersion":"` + c.fix + `","Severity":"` + c.severity + `"}]}`)
		s, err := evaluate(policy, metadata, r, at)
		if err != nil || s.Pass != c.pass || len(s.Findings) != 1 {
			t.Fatalf("fixed severity decision failed: %s", c.severity)
		}
	}
}
func TestSecretRedactionAndPrivileged(t *testing.T) {
	sentinel := "DO_NOT_EXPORT_MATCH_OR_SOURCE_948217"
	r := report(`{"Target":"ci/fixtures/example.txt","Secrets":[{"RuleID":"fixture-rule","Severity":"HIGH","Match":"` + sentinel + `","Title":"` + sentinel + `","Code":{"Lines":[{"Content":"` + sentinel + `"}]}}]}`)
	s, err := evaluate(policy, metadata, r, at)
	b, _ := json.Marshal(s)
	if err != nil || s.Pass || strings.Contains(string(b), sentinel) {
		t.Fatal("secret redaction/gate failed")
	}
	r = report(`{"Target":"rendered/pod.yaml","Misconfigurations":[{"ID":"KSV-0017","Status":"FAIL","Severity":"HIGH"},{"ID":"KSV-0001","Status":"FAIL","Severity":"MEDIUM"}]}`)
	s, err = evaluate(policy, metadata, r, at)
	if err != nil || s.Pass || s.Findings[1].Decision != "warning" {
		t.Fatal("privileged hard gate or warning separation failed")
	}
}
func TestDBUpdatedAtNotDownloadedAt(t *testing.T) {
	for _, db := range []string{`{"Version":2,"UpdatedAt":"2026-10-01T19:00:00Z","DownloadedAt":"2026-10-02T19:59:00Z"}`, `{"Version":2}`, `{"Version":1,"UpdatedAt":"2026-10-02T19:00:00Z"}`, `{"Version":2,"UpdatedAt":"2026-10-02T21:00:00Z"}`} {
		if _, err := evaluate(policy, []byte(db), report(""), at); err == nil {
			t.Fatal("unknown/old/future DB was accepted")
		}
	}
}
func TestStrictExceptionScopeAndExpiry(t *testing.T) {
	valid := `{"id":"test-only","kind":"secret","rule":"fixture-rule","package":"","path":"ci/fixtures/example.txt","owner":"Lab operator","reason":"safe synthetic test","createdAt":"2026-10-02T19:00:00Z","expiresAt":"2026-10-03T19:00:00Z"}`
	makePolicy := func(e string) []byte {
		return []byte(`{"schemaVersion":1,"trivyVersion":"0.75.0","dbMaxAgeHours":24,"exceptions":[` + e + `]}`)
	}
	r := report(`{"Target":"ci/fixtures/example.txt","Secrets":[{"RuleID":"fixture-rule","Severity":"HIGH"}]}`)
	s, err := evaluate(makePolicy(valid), metadata, r, at)
	if err != nil || !s.Pass || s.Findings[0].Decision != "excepted" {
		t.Fatal("scoped active fixture exception rejected")
	}
	for _, bad := range []string{strings.Replace(valid, "ci/fixtures/example.txt", "src/production.txt", 1), strings.Replace(valid, "2026-10-03T19:00:00Z", "2026-11-03T19:00:00Z", 1), strings.Replace(valid, "2026-10-03T19:00:00Z", "2026-10-02T19:30:00Z", 1), strings.Replace(valid, "example.txt", "*", 1), strings.Replace(valid, `"id":"test-only"`, `"id":"test-only","unexpected":true`, 1), valid + "," + valid} {
		if _, err = evaluate(makePolicy(bad), metadata, r, at); err == nil {
			t.Fatal("invalid exception accepted")
		}
	}
	if _, err = evaluate([]byte(`{"schemaVersion":1,"schemaVersion":1,"trivyVersion":"0.75.0","dbMaxAgeHours":24,"exceptions":[]}`), metadata, r, at); err == nil {
		t.Fatal("duplicate policy field accepted")
	}
}
