package service

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"

	"drop/api/internal/domain"
	"gorm.io/gorm"
)

type fixtureRoundTripper func(*http.Request) (*http.Response, error)

func (f fixtureRoundTripper) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestPostgresDiagnosisCodeRepairProtocolAndLineGate(t *testing.T) {
	for _, badLine := range []bool{false, true} {
		t.Run(fmt.Sprint(badLine), func(t *testing.T) {
			db, s, u, _ := investigationFixture(t)
			ctx := context.Background()
			revision := strings.Repeat("a", 64)
			// All executor measurements in this regression are explicit protocol
			// fixtures. Real Docker/CRM/DeepSeek evidence is recorded separately.
			executor := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var req struct {
					Action      string `json:"action"`
					ExecutionID string `json:"execution_id"`
				}
				json.NewDecoder(r.Body).Decode(&req)
				if req.Action != "describe" && req.ExecutionID == "" {
					t.Error("repair tool missing execution scope")
				}
				var data any
				switch req.Action {
				case "describe":
					data = map[string]any{"id": "crm-python", "source_revision": revision, "editable_files": []string{"palette.py"}}
				case "profile_business_flow":
					data = map[string]any{"success": true, "source_sha256": revision, "hotspots": []any{map[string]any{"path": "palette.py", "line": 31, "function": "fixture_color"}}}
				case "read_source":
					data = map[string]any{"path": "palette.py", "sha256": revision, "start_line": 1, "end_line": 32, "content": "explicit imported source protocol fixture"}
				case "test_patch":
					data = map[string]any{"success": true, "accepted": true, "functional_regression": "passed", "output_parity_cases": 384, "median_cpu_speedup": 1.5, "root_cause_status": "validated_in_controlled_crm_benchmark"}
				default:
					t.Error("unrecognized executor action")
				}
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(map[string]any{"data": data})
			}))
			defer executor.Close()
			base, _ := url.Parse(executor.URL)
			transport := executor.Client().Transport
			s.Code = &CodeExecutor{client: &http.Client{Transport: fixtureRoundTripper(func(req *http.Request) (*http.Response, error) {
				clone := req.Clone(req.Context())
				u := *req.URL
				clone.URL = &u
				clone.URL.Scheme = base.Scheme
				clone.URL.Host = base.Host
				return transport.RoundTrip(clone)
			})}}
			var calls atomic.Int64
			model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				n := calls.Add(1)
				message := map[string]any{}
				finish := "tool_calls"
				name, args := "profile_business_flow", `{}`
				if n == 2 {
					name = "read_source"
					args = `{"path":"palette.py","start_line":1,"end_line":32}`
				}
				if n == 3 {
					name = "test_patch"
					args = `{"edits":[{"path":"palette.py","sha256":"` + revision + `","old":"fixture","new":"fixed"}]}`
				}
				if n <= 3 {
					message["tool_calls"] = []any{map[string]any{"id": fmt.Sprintf("fixture-%d", n), "type": "function", "function": map[string]any{"name": name, "arguments": args}}}
				} else {
					finish = "stop"
					line := 31
					if badLine {
						line = 999
					}
					content, _ := json.Marshal(map[string]any{"summary": "显式协议fixture，未执行真实补丁。", "evidence_ids": []string{"S1", "S2", "S3"}, "locations": []any{map[string]any{"path": "palette.py", "line": line, "reason": "实际fixture热点"}}})
					message["content"] = string(content)
				}
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"finish_reason": finish, "message": message}}})
			}))
			defer model.Close()
			s.Config = AIConfig{Endpoint: model.URL, Model: "explicit-code-protocol-fixture", AllowHTTP: true}
			run, err := s.RequestCodeRepair(ctx, u, CodeRepairInput{ProjectID: "crm-python", Goal: "显式fixture测试", Consent: true}, "code-repair-protocol-fixture", "fixture")
			if err != nil {
				t.Fatal(err)
			}
			if err := s.processCodeRepair(ctx); err != nil {
				t.Fatal(err)
			}
			db.First(&run, run.ID)
			if badLine {
				if run.Status != "failed" || run.ErrorCode == nil || *run.ErrorCode != "AI_INVALID_REPORT" {
					t.Fatal("invented line accepted", run.Status, run.ErrorCode)
				}
			} else {
				if run.Status != "success" || calls.Load() != 4 {
					t.Fatal("tool loop failed", run.Status, run.ErrorCode, calls.Load())
				}
			}
		})
	}
}
func TestPostgresDiagnosisCodeRepairPermissionCancelAndAdmission(t *testing.T) {
	_, s, u, _ := investigationFixture(t)
	ctx := context.Background()
	u.Role = "operator"
	if _, err := s.RequestCodeRepair(ctx, u, CodeRepairInput{ProjectID: "crm-python", Goal: "test", Consent: true}, "code-auth-fixture", "fixture"); err == nil {
		t.Fatal("operator read source project")
	}
	u.Role = "platform_admin"
	if _, err := s.RequestCodeRepair(ctx, u, CodeRepairInput{ProjectID: "crm-python", Goal: "test", Consent: false}, "code-consent-fixture", "fixture"); err == nil {
		t.Fatal("source export without consent")
	}
	row := CodeRepair{RequestedBy: u.ID, ProjectID: "crm-python", SourceRevision: strings.Repeat("a", 64), Goal: "explicit fixture", RequestKey: "code-cancel-fixture", InputDigest: "fixture", Status: "running", Trace: json.RawMessage(`[]`)}
	owner := "fixture-owner"
	row.LeaseOwner = &owner
	if err := s.Repo.Context(ctx).Create(&row).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.CancelCodeRepair(ctx, u, row.ID, "fixture"); err != nil {
		t.Fatal(err)
	}
	if err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error { return s.codeRepairFence(ctx, tx, row, owner) }); err == nil {
		t.Fatal("canceled code worker retained lease")
	}
	if err := s.CancelCodeRepair(ctx, domain.User{ID: u.ID, Role: "viewer"}, row.ID, "fixture"); err == nil {
		t.Fatal("viewer canceled source job")
	}
}
