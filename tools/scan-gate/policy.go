// Trivy報告をpolicyへ照合し、安全な要約と通過可否を返す。ci/source-scan.shとimage検査が呼ぶ。
// 修正版ありCRITICAL・secret検出・期限切れ例外・DB/scanner異常を拒否し、注意扱いのfindingと区別する。
// policyはsecurity/scan-policy.json。原文の秘密値や不正入力を診断へ転記しない。
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"path"
	"regexp"
	"strings"
	"time"
)

type Exception struct {
	ID        string `json:"id"`
	Kind      string `json:"kind"`
	Rule      string `json:"rule"`
	Package   string `json:"package"`
	Path      string `json:"path"`
	Owner     string `json:"owner"`
	Reason    string `json:"reason"`
	CreatedAt string `json:"createdAt"`
	ExpiresAt string `json:"expiresAt"`
}
type Policy struct {
	SchemaVersion int         `json:"schemaVersion"`
	TrivyVersion  string      `json:"trivyVersion"`
	DBMaxAgeHours int         `json:"dbMaxAgeHours"`
	Exceptions    []Exception `json:"exceptions"`
}
type DB struct {
	Version   int
	UpdatedAt time.Time
}
type Vulnerability struct{ VulnerabilityID, PkgName, InstalledVersion, FixedVersion, Severity string }
type Secret struct{ RuleID, Severity string }
type Misconfiguration struct{ ID, Severity, Status string }
type Result struct {
	Target            string
	Vulnerabilities   []Vulnerability
	Secrets           []Secret
	Misconfigurations []Misconfiguration
}
type Report struct {
	SchemaVersion              int
	Trivy                      struct{ Version string }
	ArtifactType, ArtifactName string
	Results                    []Result
}
type Finding struct {
	Kind             string `json:"kind"`
	Rule             string `json:"rule"`
	Path             string `json:"path"`
	Package          string `json:"package,omitempty"`
	InstalledVersion string `json:"installedVersion,omitempty"`
	FixedVersion     string `json:"fixedVersion,omitempty"`
	Severity         string `json:"severity"`
	Decision         string `json:"decision"`
	Exception        string `json:"exception,omitempty"`
}
type Summary struct {
	SchemaVersion int       `json:"schemaVersion"`
	Pass          bool      `json:"pass"`
	TrivyVersion  string    `json:"trivyVersion"`
	DBUpdatedAt   time.Time `json:"dbUpdatedAt"`
	ScannedAt     time.Time `json:"scannedAt"`
	Findings      []Finding `json:"findings"`
}

var publicIdentifier = regexp.MustCompile(`^[A-Za-z0-9@._/+:-]{1,240}$`)
var publicVersion = regexp.MustCompile(`^[A-Za-z0-9._+,:~ /()-]{1,240}$`)

func safePath(s string) bool {
	return publicIdentifier.MatchString(s) && !strings.HasPrefix(s, "/") && path.Clean(s) == s && s != ".." && !strings.HasPrefix(s, "../") && !strings.Contains(s, ":")
}
func validSeverity(s string) bool {
	return s == "UNKNOWN" || s == "LOW" || s == "MEDIUM" || s == "HIGH" || s == "CRITICAL"
}

// 型付きdecode前に全階層の重複キーを拒否する。キー/path/raw値/decoder診断はエラーメッセージへ含めない。
func uniqueJSON(data []byte) error {
	d := json.NewDecoder(bytes.NewReader(data))
	var value func() error
	value = func() error {
		t, err := d.Token()
		if err != nil {
			return errors.New("invalid JSON")
		}
		if delim, ok := t.(json.Delim); ok {
			switch delim {
			case '{':
				keys := map[string]bool{}
				for d.More() {
					k, e := d.Token()
					if e != nil {
						return errors.New("invalid JSON")
					}
					key, ok := k.(string)
					if !ok || keys[key] {
						return errors.New("duplicate or invalid JSON key")
					}
					keys[key] = true
					if e = value(); e != nil {
						return e
					}
				}
			case '[':
				for d.More() {
					if e := value(); e != nil {
						return e
					}
				}
			default:
				return errors.New("invalid JSON structure")
			}
			if _, e := d.Token(); e != nil {
				return errors.New("invalid JSON")
			}
		}
		return nil
	}
	if err := value(); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return errors.New("trailing JSON")
	}
	return nil
}
func decode(data []byte, dst any, strict bool) error {
	if len(data) > 32<<20 {
		return errors.New("input size exceeded")
	}
	if err := uniqueJSON(data); err != nil {
		return err
	}
	d := json.NewDecoder(bytes.NewReader(data))
	if strict {
		d.DisallowUnknownFields()
	}
	if err := d.Decode(dst); err != nil {
		return errors.New("input schema rejected")
	}
	return nil
}
func evaluate(policyData, dbData, reportData []byte, now time.Time) (Summary, error) {
	var p Policy
	var db DB
	var r Report
	s := Summary{SchemaVersion: 1, ScannedAt: now, Findings: []Finding{}}
	if err := decode(policyData, &p, true); err != nil {
		return s, err
	}
	if p.SchemaVersion != 1 || p.TrivyVersion != "0.75.0" || p.DBMaxAgeHours != 24 {
		return s, errors.New("fixed policy contract rejected")
	}
	ids := map[string]bool{}
	for _, e := range p.Exceptions {
		created, ce := time.Parse(time.RFC3339, e.CreatedAt)
		expires, ee := time.Parse(time.RFC3339, e.ExpiresAt)
		if !publicIdentifier.MatchString(e.ID) || ids[e.ID] || !publicIdentifier.MatchString(e.Rule) || !safePath(e.Path) || strings.TrimSpace(e.Owner) == "" || strings.TrimSpace(e.Reason) == "" || ce != nil || ee != nil || created.After(now) || !expires.After(now) || !expires.After(created) || expires.Sub(created) > 14*24*time.Hour {
			return s, errors.New("exception scope or expiry rejected")
		}
		ids[e.ID] = true
		switch e.Kind {
		case "vulnerability":
			if !publicIdentifier.MatchString(e.Package) {
				return s, errors.New("exception package rejected")
			}
		case "secret":
			if !strings.HasPrefix(e.Path, "ci/fixtures/") || e.Package != "" {
				return s, errors.New("secret exception outside test fixture")
			}
		case "misconfiguration":
			if e.Rule == "KSV-0017" || e.Package != "" {
				return s, errors.New("forbidden misconfiguration exception")
			}
		default:
			return s, errors.New("exception kind rejected")
		}
	}
	if err := decode(dbData, &db, false); err != nil {
		return s, err
	}
	if db.Version != 2 || db.UpdatedAt.IsZero() || db.UpdatedAt.After(now) || now.Sub(db.UpdatedAt) > 24*time.Hour {
		return s, errors.New("DB unknown, future, or expired")
	}
	if err := decode(reportData, &r, false); err != nil {
		return s, err
	}
	if r.SchemaVersion != 2 || r.Trivy.Version != p.TrivyVersion || (r.ArtifactType != "filesystem" && r.ArtifactType != "repository") {
		return s, errors.New("scanner report identity rejected")
	}
	s.TrivyVersion = p.TrivyVersion
	s.DBUpdatedAt = db.UpdatedAt
	s.Pass = true
	add := func(f Finding, block bool) error {
		if !safePath(f.Path) || !publicIdentifier.MatchString(f.Rule) || !validSeverity(f.Severity) || (f.Package != "" && !publicIdentifier.MatchString(f.Package)) || (f.InstalledVersion != "" && !publicVersion.MatchString(f.InstalledVersion)) || (f.FixedVersion != "" && !publicVersion.MatchString(f.FixedVersion)) {
			return errors.New("finding identifier rejected")
		}
		f.Decision = "warning"
		if block {
			f.Decision = "fail"
		}
		for _, e := range p.Exceptions {
			if e.Kind == f.Kind && e.Rule == f.Rule && e.Path == f.Path && e.Package == f.Package {
				f.Decision = "excepted"
				f.Exception = e.ID
				block = false
				break
			}
		}
		if block {
			s.Pass = false
		}
		s.Findings = append(s.Findings, f)
		return nil
	}
	for _, result := range r.Results {
		target := result.Target
		if strings.HasPrefix(target, r.ArtifactName+"/") {
			target = strings.TrimPrefix(target, r.ArtifactName+"/")
		}
		// Trivy archive内の位置は安全な相対疑似pathへ正規化する。通常のcolon/URL/drive形式のtargetは引き続き拒否する。
		for _, ext := range []string{".tgz:", ".tar:", ".zip:"} {
			target = strings.ReplaceAll(target, ext, strings.TrimSuffix(ext, ":")+"/")
		}
		for _, v := range result.Vulnerabilities {
			f := Finding{Kind: "vulnerability", Rule: v.VulnerabilityID, Path: target, Package: v.PkgName, InstalledVersion: v.InstalledVersion, FixedVersion: v.FixedVersion, Severity: v.Severity}
			if err := add(f, v.Severity == "CRITICAL" && v.FixedVersion != ""); err != nil {
				return Summary{}, err
			}
		}
		for _, v := range result.Secrets {
			if err := add(Finding{Kind: "secret", Rule: v.RuleID, Path: target, Severity: v.Severity}, true); err != nil {
				return Summary{}, err
			}
		}
		for _, v := range result.Misconfigurations {
			if v.Status != "FAIL" {
				continue
			}
			if err := add(Finding{Kind: "misconfiguration", Rule: v.ID, Path: target, Severity: v.Severity}, v.ID == "KSV-0017"); err != nil {
				return Summary{}, err
			}
		}
	}
	return s, nil
}
