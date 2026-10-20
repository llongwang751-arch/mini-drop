// 本文件验证公开日志授权的字段白名单、固定路径和跨 attempt 拒绝规则。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package repository

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"drop/api/internal/domain"
)

func logGrantFixture() domain.Attempt {
	agent := "imported-test-agent"
	return domain.Attempt{TaskID: 17, Attempt: 2, AgentID: &agent, UploadTargets: json.RawMessage(`[
		{"name":"target.elf","kind":"BINARY","object_key":"raw/tasks/17/attempts/2/target.elf","put_url":"fixture-only-url"},
		{"name":"collection-manifest.json","kind":"MANIFEST","object_key":"manifest/tasks/17/attempts/2/collection-manifest.json","put_url":"fixture-only-url","format":"collection_manifest_v1","content_type":"application/json","max_file_bytes":65536},
		{"name":"runner.log","kind":"LOG","object_key":"raw/tasks/17/attempts/2/runner.log","put_url":"fixture-only-url","format":"log_jsonl","content_type":"text/plain","max_file_bytes":16384,"untrusted_extra":"must-not-survive"}
	]`)}
}

func TestTerminalUploadMetadataPreservesOnlyFixedLogAuthorization(t *testing.T) {
	a := logGrantFixture()
	actual := TerminalUploadMetadata(a)
	var grants []map[string]any
	if err := json.Unmarshal(actual, &grants); err != nil || len(grants) != 1 {
		t.Fatal("expected exactly one bounded LOG authorization without upload URL")
	}
	grant := grants[0]
	if len(grant) != 6 || grant["name"] != "runner.log" || grant["kind"] != "LOG" || grant["object_key"] != "raw/tasks/17/attempts/2/runner.log" || grant["format"] != "log_jsonl" || grant["content_type"] != "text/plain" || grant["max_file_bytes"] != float64(16384) {
		t.Fatal("terminal LOG authorization changed its exact identity or limit")
	}
	for _, forbidden := range []string{"put_url", "fixture-only-url", "untrusted_extra", "BINARY", "target.elf", "MANIFEST", "collection-manifest.json"} {
		if strings.Contains(string(actual), forbidden) {
			t.Fatal("terminal metadata retained a URL, arbitrary attachment, or extra field")
		}
	}
	if string(TerminalUploadMetadata(domain.Attempt{TaskID: a.TaskID, Attempt: a.Attempt, AgentID: a.AgentID, UploadTargets: actual})) != string(actual) {
		t.Fatal("terminal metadata filter is not idempotent")
	}
}

func TestTerminalUploadMetadataRejectsInvalidOrCrossAttemptGrants(t *testing.T) {
	valid := map[string]any{"name": "runner.log", "kind": "LOG", "object_key": "raw/tasks/17/attempts/2/runner.log", "format": "log_jsonl", "content_type": "text/plain", "max_file_bytes": 16384}
	for name, changes := range map[string]map[string]any{
		"different task":       {"object_key": "raw/tasks/18/attempts/2/runner.log"},
		"different attempt":    {"object_key": "raw/tasks/17/attempts/1/runner.log"},
		"path traversal":       {"object_key": "raw/tasks/17/attempts/2/../runner.log"},
		"different basename":   {"name": "stderr.log"},
		"raw disguised as log": {"kind": "RAW"},
		"unbounded log":        {"max_file_bytes": 16385},
		"wrong format":         {"format": "json"},
		"wrong content type":   {"content_type": "text/html"},
		"noninteger size":      {"max_file_bytes": 16384.5},
	} {
		t.Run(name, func(t *testing.T) {
			grant := map[string]any{}
			for k, v := range valid {
				grant[k] = v
			}
			for k, v := range changes {
				grant[k] = v
			}
			a := logGrantFixture()
			a.UploadTargets, _ = json.Marshal([]any{grant})
			if string(TerminalUploadMetadata(a)) != "[]" {
				t.Fatal("invalid or cross-attempt LOG grant survived")
			}
		})
	}
	for i, input := range []string{"", "null", `{}`, `[`, `[null]`, `[{"name":"runner.log"}]`, `[{"max_file_bytes":"16384"}]`, strings.Repeat(" ", 65537)} {
		t.Run(fmt.Sprintf("malformed-%d", i), func(t *testing.T) {
			a := logGrantFixture()
			a.UploadTargets = json.RawMessage(input)
			if string(TerminalUploadMetadata(a)) != "[]" {
				t.Fatal("malformed grants survived")
			}
		})
	}
	for _, noAgent := range []bool{false, true} {
		a := logGrantFixture()
		if noAgent {
			a.AgentID = nil
		} else {
			a.Attempt = 0
		}
		if string(TerminalUploadMetadata(a)) != "[]" {
			t.Fatal("unbound/import authorization survived")
		}
	}
	duplicate := logGrantFixture()
	duplicate.UploadTargets, _ = json.Marshal([]any{valid, valid})
	if string(TerminalUploadMetadata(duplicate)) != "[]" {
		t.Fatal("ambiguous duplicate LOG authorizations survived")
	}
}
