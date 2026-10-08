package main

import (
	release "core-platform/release-record"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type outcome struct {
	Case     string `json:"case"`
	Refused  bool   `json:"refused"`
	Boundary string `json:"boundary"`
}

func main() {
	if len(os.Args) != 2 {
		panic("public bundle directory required")
	}
	read := func(name string) []byte {
		b, e := os.ReadFile(filepath.Join(os.Args[1], name+".json"))
		if e != nil {
			panic("public bundle unavailable")
		}
		return b
	}
	rb, sb, bb := read("record"), read("scan"), read("sbom")
	now := time.Now().UTC()
	baseline, e := release.ValidateBundle(rb, sb, bb, now)
	if e != nil || baseline.SourceProjectID != 86247033 || baseline.ScanPipelineID != 2911071988 || baseline.ScanJobID != 16922783996 || baseline.Decision != "passed" || baseline.BuildJobID != 16922783993 {
		panic("actual public positive bundle refused or identity changed")
	}
	var outcomes []outcome
	run := func(name, boundary string, r []byte, s []byte, b []byte) {
		_, err := release.ValidateBundle(r, s, b, now)
		if err == nil {
			panic("explicit local evidence substitution accepted: " + name)
		}
		outcomes = append(outcomes, outcome{name, true, boundary})
	}
	recordCase := func(name string, modify func(*release.Record)) {
		r, err := release.DecodeRecord(rb)
		if err != nil {
			panic("baseline decode failed")
		}
		modify(&r)
		encoded, err := json.Marshal(r)
		if err != nil {
			panic("fixture encode failed")
		}
		run(name, "record/run descriptor binding", encoded, sb, bb)
	}
	recordCase("digest-substitution", func(r *release.Record) { r.ImageDigest = "sha256:" + strings.Repeat("0", 64) })
	recordCase("scan-pipeline-substitution", func(r *release.Record) { r.ScanPipelineID++ })
	recordCase("scan-job-substitution", func(r *release.Record) { r.ScanJobID++ })
	recordCase("sbom-descriptor-job-substitution", func(r *release.Record) { r.SBOM.ScanJobID++ })
	recordCase("sbom-url-other-run", func(r *release.Record) { r.SBOM.URL = strings.Replace(r.SBOM.URL, "16922783996", "16922783997", 1) })
	recordCase("scan-url-other-run", func(r *release.Record) {
		r.ScanReport.URL = strings.Replace(r.ScanReport.URL, "16922783996", "16922783997", 1)
	})
	tampered := append([]byte(nil), bb...)
	tampered = append(tampered, ' ')
	run("sbom-body-checksum-substitution", "original stored SBOM checksum", rb, sb, tampered)
	var invalid map[string]any
	if json.Unmarshal(bb, &invalid) != nil {
		panic("actual SBOM decode failed")
	}
	invalid["components"] = "explicit-local-invalid-schema-fixture"
	invalidBytes, err := json.Marshal(invalid)
	if err != nil {
		panic("schema fixture encode failed")
	}
	r, err := release.DecodeRecord(rb)
	if err != nil {
		panic("record decode failed")
	}
	r.SBOM.SHA256 = release.Checksum(invalidBytes)
	invalidRecord, err := json.Marshal(r)
	if err != nil {
		panic("record fixture encode failed")
	}
	run("invalid-sbom-schema-with-recomputed-checksum", "full official CycloneDX schema", invalidRecord, sb, invalidBytes)
	if baseline.Adopt(now.Add(25*time.Hour)) == nil {
		panic("explicit future-time expiry fixture accepted")
	}
	outcomes = append(outcomes, outcome{"expiry-at-simulated-now-plus-25-hours", true, "explicit clock fixture; not elapsed real time"})
	output := struct {
		UTC                     time.Time `json:"utc"`
		Source                  string    `json:"source"`
		ScanPipeline            int64     `json:"scanPipeline"`
		ScanJob                 int64     `json:"scanJob"`
		BuildJob                int64     `json:"buildJob"`
		Digest                  string    `json:"digest"`
		RecordHash              string    `json:"actualOriginalRecordSha256"`
		ScanHash                string    `json:"actualOriginalScanSha256"`
		SBOMHash                string    `json:"actualOriginalSbomSha256"`
		PositiveBundleValidated bool      `json:"positiveBundleValidated"`
		Scope                   string    `json:"scope"`
		ServerDataMutated       bool      `json:"serverDataMutated"`
		NewDatabaseClaimed      bool      `json:"newDatabaseClaimed"`
		AdoptionAuthorized      bool      `json:"adoptionAuthorized"`
		UnitAccepted            bool      `json:"unitAccepted"`
		Outcomes                []outcome `json:"outcomes"`
	}{now, baseline.SourceCommit, baseline.ScanPipelineID, baseline.ScanJobID, baseline.BuildJobID, baseline.ImageDigest, release.Checksum(rb), release.Checksum(sb), release.Checksum(bb), true, "Local explicit mutations of checksum-verified actual safe public Package bytes; production backend validator; not a new scan or server upload", false, false, false, false, outcomes}
	encoded, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		panic("proof encode failed")
	}
	fmt.Println(string(encoded))
}
