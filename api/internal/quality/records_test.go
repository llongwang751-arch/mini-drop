package quality

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// 所有载荷是明确导入的合成夹具，只验证发布摘要的解析边界，不冒充实验实跑。
func fixture() Run {
	return Run{SchemaVersion: 1, ID: "fixture-run-001", Suite: "fixture-suite", Title: "导入的合成执行", Category: "system", Status: "passed", StartedAt: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), FinishedAt: time.Date(2026, 1, 1, 0, 0, 1, 0, time.UTC), Environment: "linux_isolated", SourceSHA256: strings.Repeat("a", 64), Provenance: "imported_fixture", FixtureScope: "明确导入的合成夹具", Counts: Counts{Passed: 1, Total: 1}, Metrics: []Metric{}, Checks: []Check{}, Limits: []string{"不代表真实实验或独立人工评审"}}
}
func save(t *testing.T, dir, id string, data []byte) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, id+".json"), data, 0600); err != nil {
		t.Fatal(err)
	}
}
func TestImmutableReadOrderingAndDigest(t *testing.T) {
	dir := t.TempDir()
	one := fixture()
	data, _ := json.Marshal(one)
	save(t, dir, one.ID, data)
	two := one
	two.ID = "fixture-run-002"
	two.FinishedAt = two.FinishedAt.Add(time.Second)
	data, _ = json.Marshal(two)
	save(t, dir, two.ID, data)
	rows, err := List(dir)
	if err != nil || len(rows) != 2 || rows[0].Run.ID != two.ID || len(rows[0].RecordSHA256) != 64 {
		t.Fatal("invalid sorted receipts", err)
	}
	item, err := Get(dir, two.ID)
	if err != nil || item.RecordSHA256 != rows[0].RecordSHA256 {
		t.Fatal("detail digest mismatch")
	}
}
func TestParserRejectsAmbiguousAndPrivateRecords(t *testing.T) {
	base, _ := json.Marshal(fixture())
	cases := map[string][]byte{
		"null-detail":        []byte(strings.Replace(string(base), `"checks":[]`, `"checks":[{"id":"null-check","label":"Null","status":"passed","detail":null}]`, 1)),
		"private-dsn":        []byte(strings.Replace(string(base), "明确导入的合成夹具", "postgresql://fixture:secret@db", 1)),
		"null-count":         []byte(strings.Replace(string(base), `"failed":0`, `"failed":null`, 1)),
		"float-count":        []byte(strings.Replace(string(base), `"passed":1`, `"passed":1.0`, 1)),
		"duplicate":          append([]byte(`{"id":"other-run",`), base[1:]...),
		"unknown":            append([]byte(`{"credential":"never-expose",`), base[1:]...),
		"trailing":           append(append([]byte{}, base...), []byte(` {}`)...),
		"oversize":           []byte(strings.Repeat(" ", MaxBytes+1)),
		"invalid-utf8":       append(append([]byte{}, base...), 0xff),
		"missing-null-field": []byte(strings.Replace(string(base), `"source_commit":null,`, "", 1)),
		"missing-zero-count": []byte(strings.Replace(string(base), `"failed":0,`, "", 1)),
		"private-url":        []byte(strings.Replace(string(base), "明确导入的合成夹具", "https://fixture.invalid/signed", 1)),
		"skip-as-success":    []byte(strings.Replace(string(base), `"skipped":0`, `"skipped":1`, 1)),
	}
	for name, data := range cases {
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			save(t, dir, fixture().ID, data)
			if _, err := Get(dir, fixture().ID); err == nil {
				t.Fatal("unsafe record accepted")
			}
		})
	}
}
func TestRecordSourceAndPathBoundaries(t *testing.T) {
	if rows, err := List(""); err != nil || len(rows) != 0 {
		t.Fatal("unconfigured directory should be empty")
	}
	if _, err := List(filepath.Join(t.TempDir(), "missing")); err == nil {
		t.Fatal("configured missing directory hidden")
	}
	dir := t.TempDir()
	data, _ := json.Marshal(fixture())
	save(t, dir, "different-id", data)
	if _, err := Get(dir, "different-id"); err == nil {
		t.Fatal("filename and id mismatch")
	}
	for _, id := range []string{"../fixture", "/etc/passwd", "fixture.json", "short"} {
		if _, err := Get(dir, id); err == nil {
			t.Fatal("path accepted")
		}
	}
}
func TestCounterAndUnknownMetricBoundaries(t *testing.T) {
	r := fixture()
	r.Checks = []Check{{ID: "failed-check", Label: "失败不能被总通过掩盖", Status: "failed", Detail: ""}}
	if r.Validate() == nil {
		t.Fatal("failed critical check hidden by passed summary")
	}
	r.Checks = []Check{}
	r.Metrics = []Metric{{ID: "human-accuracy", Label: "人工准确率", Value: nil, Unit: "ratio"}}
	if r.Validate() != nil {
		t.Fatal("unknown must remain null")
	}
	r.Counts = Counts{Passed: 0, Skipped: 1, Total: 1}
	if r.Validate() == nil {
		t.Fatal("skip is not success")
	}
	r.Status = "incomplete"
	if r.Validate() != nil {
		t.Fatal("incomplete rejected")
	}
	r.Status = "failed"
	if r.Validate() == nil {
		t.Fatal("failure requires explicit failed denominator")
	}
}
