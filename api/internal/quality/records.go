// Package quality 读取离线质量实验的审阅摘要，不执行测试，也不接触任务、模型或存储凭据。
// 发布器先校验、原子写入新文件；API只读挂载目录。记录SHA用于核对字节，不冒充数字签名。
package quality

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"os"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

const MaxRecords = 128
const MaxBytes = 32768

var ErrInvalid = errors.New("invalid quality record")
var ErrUnavailable = errors.New("quality records unavailable")
var idPattern = regexp.MustCompile(`^[a-z][a-z0-9-]{7,79}$`)
var shaPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var commitPattern = regexp.MustCompile(`^[a-f0-9]{40}$`)
var privatePattern = regexp.MustCompile(`(?i)(sk-[a-z0-9]{20,}|-----BEGIN|[a-z][a-z0-9+.-]{1,20}://|x-amz-|authorization:|bearer |password=|cookie:)`)

type Counts struct {
	Passed  int `json:"passed"`
	Failed  int `json:"failed"`
	Skipped int `json:"skipped"`
	Total   int `json:"total"`
}
type Metric struct {
	ID    string   `json:"id"`
	Label string   `json:"label"`
	Value *float64 `json:"value"`
	Unit  string   `json:"unit"`
}
type Check struct {
	ID     string `json:"id"`
	Label  string `json:"label"`
	Status string `json:"status"`
	Detail string `json:"detail"`
}
type Run struct {
	SchemaVersion int       `json:"schema_version"`
	ID            string    `json:"id"`
	Title         string    `json:"title"`
	Category      string    `json:"category"`
	Suite         string    `json:"suite"`
	Status        string    `json:"status"`
	StartedAt     time.Time `json:"started_at"`
	FinishedAt    time.Time `json:"finished_at"`
	Environment   string    `json:"environment"`
	SourceCommit  *string   `json:"source_commit"`
	SourceSHA256  string    `json:"source_sha256"`
	Provenance    string    `json:"provenance"`
	FixtureScope  string    `json:"fixture_scope"`
	Counts        Counts    `json:"counts"`
	Metrics       []Metric  `json:"metrics"`
	Checks        []Check   `json:"checks"`
	Limits        []string  `json:"limits"`
}
type Record struct {
	Run          Run    `json:"run"`
	RecordSHA256 string `json:"record_sha256"`
}

func oneOf(v string, choices ...string) bool {
	for _, choice := range choices {
		if v == choice {
			return true
		}
	}
	return false
}
func text(v string, max int, empty bool) bool {
	return (empty || strings.TrimSpace(v) != "") && len([]rune(v)) <= max && !strings.ContainsFunc(v, unicode.IsControl) && !privatePattern.MatchString(v)
}

// Validate 把状态、计数、时间和数值作为一份记录核对；跳过不能提升为全通过。
// 这是展示摘要的边界校验，不重新执行报告里声称的实验，更不验证人工审阅者身份。
func (r Run) Validate() error {
	c := r.Counts
	if r.SchemaVersion != 1 || !idPattern.MatchString(r.ID) || !idPattern.MatchString(r.Suite) || !text(r.Title, 120, false) || !text(r.FixtureScope, 240, false) || !shaPattern.MatchString(r.SourceSHA256) {
		return ErrInvalid
	}
	if r.SourceCommit != nil && !commitPattern.MatchString(*r.SourceCommit) {
		return ErrInvalid
	}
	if !oneOf(r.Category, "business", "agent", "security", "protocol", "system", "performance", "release", "client") || !oneOf(r.Status, "passed", "failed", "incomplete") || !oneOf(r.Environment, "windows_local", "linux_isolated", "github_ci", "production_bounded") || !oneOf(r.Provenance, "actual_execution", "imported_fixture") {
		return ErrInvalid
	}
	if r.StartedAt.IsZero() || r.FinishedAt.Before(r.StartedAt) || r.FinishedAt.Sub(r.StartedAt) > 7*24*time.Hour || r.FinishedAt.After(time.Now().Add(time.Hour)) {
		return ErrInvalid
	}
	if c.Passed < 0 || c.Failed < 0 || c.Skipped < 0 || c.Passed > 10000000 || c.Failed > 10000000 || c.Skipped > 10000000 || c.Total < 1 || c.Total > 10000000 || c.Passed+c.Failed+c.Skipped != c.Total {
		return ErrInvalid
	}
	if r.Status == "passed" && (c.Failed != 0 || c.Skipped != 0) || r.Status == "failed" && c.Failed == 0 || r.Status == "incomplete" && (c.Skipped == 0 || c.Failed != 0) {
		return ErrInvalid
	}
	if r.Metrics == nil || r.Checks == nil || r.Limits == nil || len(r.Metrics) > 16 || len(r.Checks) > 64 || len(r.Limits) < 1 || len(r.Limits) > 16 {
		return ErrInvalid
	}
	seen := map[string]bool{}
	for _, m := range r.Metrics {
		if !idPattern.MatchString(m.ID) || seen[m.ID] || !text(m.Label, 64, false) || !oneOf(m.Unit, "count", "ms", "s", "ratio", "percent", "bytes", "tokens", "multiple") {
			return ErrInvalid
		}
		seen[m.ID] = true
		if m.Value != nil && (math.IsNaN(*m.Value) || math.IsInf(*m.Value, 0) || *m.Value < 0 || m.Unit == "ratio" && *m.Value > 1 || m.Unit == "percent" && *m.Value > 100) {
			return ErrInvalid
		}
	}
	seen = map[string]bool{}
	for _, c := range r.Checks {
		if r.Status == "passed" && c.Status != "passed" {
			return ErrInvalid
		}
		if !idPattern.MatchString(c.ID) || seen[c.ID] || !text(c.Label, 80, false) || !text(c.Detail, 240, true) || !oneOf(c.Status, "passed", "failed", "skipped") {
			return ErrInvalid
		}
		seen[c.ID] = true
	}
	for _, limit := range r.Limits {
		if !text(limit, 240, false) {
			return ErrInvalid
		}
	}
	return nil
}

// uniqueJSON 拒绝重复对象键，防止发布器和Go解码器读取同一文件却采用不同字段。
func uniqueJSON(d *json.Decoder, depth int) error {
	if depth > 12 {
		return ErrInvalid
	}
	t, err := d.Token()
	if err != nil {
		return ErrInvalid
	}
	switch t {
	case json.Delim('{'):
		seen := map[string]bool{}
		for d.More() {
			key, err := d.Token()
			s, ok := key.(string)
			if err != nil || !ok || seen[s] {
				return ErrInvalid
			}
			seen[s] = true
			if uniqueJSON(d, depth+1) != nil {
				return ErrInvalid
			}
		}
		if t, e := d.Token(); e != nil || t != json.Delim('}') {
			return ErrInvalid
		}
	case json.Delim('['):
		for d.More() {
			if uniqueJSON(d, depth+1) != nil {
				return ErrInvalid
			}
		}
		if t, e := d.Token(); e != nil || t != json.Delim(']') {
			return ErrInvalid
		}
	}
	return nil
}

func fields(data json.RawMessage, names ...string) bool {
	var object map[string]json.RawMessage
	if json.Unmarshal(data, &object) != nil || len(object) != len(names) {
		return false
	}
	for _, name := range names {
		if _, ok := object[name]; !ok {
			return false
		}
		if bytes.Equal(bytes.TrimSpace(object[name]), []byte("null")) && name != "source_commit" && name != "value" {
			return false
		}
	}
	return true
}
func requiredShape(data []byte) bool {
	if !fields(data, "schema_version", "id", "title", "category", "suite", "status", "started_at", "finished_at", "environment", "source_commit", "source_sha256", "provenance", "fixture_scope", "counts", "metrics", "checks", "limits") {
		return false
	}
	var object map[string]json.RawMessage
	_ = json.Unmarshal(data, &object)
	if !fields(object["counts"], "passed", "failed", "skipped", "total") {
		return false
	}
	for _, key := range []string{"metrics", "checks"} {
		var items []json.RawMessage
		if json.Unmarshal(object[key], &items) != nil {
			return false
		}
		for _, item := range items {
			if key == "metrics" && !fields(item, "id", "label", "value", "unit") || key == "checks" && !fields(item, "id", "label", "status", "detail") {
				return false
			}
		}
	}
	return true
}

func read(root *os.Root, id string) (Record, error) {
	if !idPattern.MatchString(id) {
		return Record{}, ErrInvalid
	}
	f, err := openRecord(root, id+".json")
	if err != nil {
		if os.IsNotExist(err) {
			return Record{}, os.ErrNotExist
		}
		return Record{}, ErrUnavailable
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > MaxBytes {
		return Record{}, ErrInvalid
	}
	data, err := io.ReadAll(io.LimitReader(f, MaxBytes+1))
	if err != nil || len(data) > MaxBytes || !utf8.Valid(data) {
		return Record{}, ErrInvalid
	}
	d := json.NewDecoder(bytes.NewReader(data))
	if uniqueJSON(d, 0) != nil {
		return Record{}, ErrInvalid
	}
	if _, err := d.Token(); err != io.EOF {
		return Record{}, ErrInvalid
	}
	var run Run
	d = json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if !requiredShape(data) || d.Decode(&run) != nil || run.ID != id || run.Validate() != nil {
		return Record{}, ErrInvalid
	}
	h := sha256.Sum256(data)
	return Record{Run: run, RecordSHA256: hex.EncodeToString(h[:])}, nil
}

func Get(directory, id string) (Record, error) {
	if directory == "" {
		return Record{}, os.ErrNotExist
	}
	root, err := os.OpenRoot(directory)
	if err != nil {
		return Record{}, ErrUnavailable
	}
	defer root.Close()
	return read(root, id)
}
func List(directory string) ([]Record, error) {
	result := []Record{}
	if directory == "" {
		return result, nil
	}
	root, err := os.OpenRoot(directory)
	if err != nil {
		return nil, ErrUnavailable
	}
	defer root.Close()
	f, err := root.Open(".")
	if err != nil {
		return nil, ErrUnavailable
	}
	defer f.Close()
	entries, err := f.ReadDir(MaxRecords + 1)
	if err != nil && err != io.EOF {
		return nil, ErrUnavailable
	}
	if len(entries) > MaxRecords {
		return nil, ErrInvalid
	}
	for _, entry := range entries {
		if !entry.Type().IsRegular() || !strings.HasSuffix(entry.Name(), ".json") {
			return nil, ErrInvalid
		}
		record, err := read(root, strings.TrimSuffix(entry.Name(), ".json"))
		if err != nil {
			return nil, ErrInvalid
		}
		result = append(result, record)
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Run.FinishedAt.Equal(result[j].Run.FinishedAt) {
			return result[i].Run.ID < result[j].Run.ID
		}
		return result[i].Run.FinishedAt.After(result[j].Run.FinishedAt)
	})
	return result, nil
}
