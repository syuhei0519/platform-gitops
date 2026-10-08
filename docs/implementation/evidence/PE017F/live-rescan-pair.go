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

func main() {
	if len(os.Args) != 2 {
		panic("public bundle directory required")
	}
	read := func(p string) []byte {
		b, e := os.ReadFile(filepath.Join(os.Args[1], p))
		if e != nil {
			panic("actual public file unavailable")
		}
		return b
	}
	firstBytes := read("record.json")
	secondBytes := read("second/record.json")
	scanBytes := read("second/scan.json")
	now := time.Now().UTC()
	first, e := release.DecodeRecord(firstBytes)
	if e != nil {
		panic("first actual record invalid")
	}
	second, e := release.ValidateBundle(secondBytes, scanBytes, nil, now)
	if e != nil {
		panic("second actual failed bundle invalid")
	}
	_, pair, e := release.ValidateRescanPair(second, firstBytes, now)
	if e != nil || !pair.SameImageAndBuild || !pair.DifferentDatabase || !pair.CurrentFailed || pair.PriorSuccessFallback || !second.Database.UpdatedAt.After(first.Database.UpdatedAt) || second.Adopt(now) == nil || first.ScanJobID != 16911641951 || second.ScanJobID != 16911641952 || second.BuildJobID != 16911641948 || second.ScanPipelineID != 2908988271 {
		panic("actual pair/failure adoption guard refused")
	}
	type result struct {
		Case              string `json:"case"`
		AcceptanceRefused bool   `json:"acceptanceRefused"`
	}
	var cases []result
	check := func(name string, mutate func(*release.Record)) {
		r, err := release.DecodeRecord(secondBytes)
		if err != nil {
			panic("second decode invalid")
		}
		mutate(&r)
		_, _, err = release.ValidateRescanPair(r, firstBytes, now)
		if err == nil {
			panic("actual pair substitution accepted: " + name)
		}
		cases = append(cases, result{name, true})
	}
	check("original-build-job-substitution", func(r *release.Record) { r.BuildJobID++ })
	check("original-build-pipeline-substitution", func(r *release.Record) { r.BuildPipelineID++ })
	check("original-archive-substitution", func(r *release.Record) { r.InputArchiveSHA256 = strings.Repeat("0", 64) })
	check("policy-checksum-substitution", func(r *release.Record) { r.PolicySHA256 = strings.Repeat("0", 64) })
	check("source-substitution", func(r *release.Record) { r.SourceCommit = strings.Repeat("0", 40); r.ImageTag = r.SourceCommit })
	check("scan-job-reused-from-first", func(r *release.Record) {
		r.ScanJobID = first.ScanJobID
		r.ScanReport.ScanJobID = r.ScanJobID
		r.ScanReport.URL = strings.Replace(r.ScanReport.URL, "16911641952", "16911641951", 1)
	})
	check("digest-substitution-with-matching-descriptor", func(r *release.Record) {
		old := r.ImageDigest
		r.ImageDigest = "sha256:" + strings.Repeat("0", 64)
		r.ScanReport.ImageDigest = r.ImageDigest
		r.ScanReport.URL = strings.Replace(r.ScanReport.URL, old[7:], r.ImageDigest[7:], 1)
	})
	check("scan-time-not-later-than-first", func(r *release.Record) { r.ScannedAt = first.ScannedAt })
	unchanged := second
	unchanged.Database = first.Database
	_, same, e := release.ValidateRescanPair(unchanged, firstBytes, now)
	if e != nil || same.DifferentDatabase {
		panic("unchanged DB evidence incorrectly marked AT04")
	}
	cases = append(cases, result{"unchanged-db-cannot-satisfy-AT04", true})
	output := struct {
		UTC                               time.Time           `json:"utc"`
		Source                            string              `json:"source"`
		FirstRecordSHA                    string              `json:"firstRecordSha256"`
		SecondRecordSHA                   string              `json:"secondRecordSha256"`
		SecondScanSHA                     string              `json:"secondScanSha256"`
		FirstDB                           time.Time           `json:"firstDbUpdatedAt"`
		SecondDB                          time.Time           `json:"secondDbUpdatedAt"`
		Pair                              release.RescanProof `json:"actualPair"`
		CurrentFailedAdoptionRefused      bool                `json:"currentFailedAdoptionRefused"`
		HistoricalFirstUsedOnlyAsEvidence bool                `json:"historicalFirstUsedOnlyAsEvidence"`
		Scope                             string              `json:"scope"`
		UnitAccepted                      bool                `json:"unitAccepted"`
		Cases                             []result            `json:"cases"`
	}{now, second.SourceCommit, release.Checksum(firstBytes), release.Checksum(secondBytes), release.Checksum(scanBytes), first.Database.UpdatedAt, second.Database.UpdatedAt, pair, true, true, "Checksum-verified actual immutable Package pair; local explicit substitution fixtures; no server or database timestamp mutation", false, cases}
	b, e := json.MarshalIndent(output, "", "  ")
	if e != nil {
		panic("proof encode failed")
	}
	fmt.Println(string(b))
}
