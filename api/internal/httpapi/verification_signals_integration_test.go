// 复测结果会复制候选诊断报告，必须在这个HTTP出口重新检查每个来源的业务信号授权。
// 数据与报告均为明确导入的合成协议夹具；只运行真实PostgreSQL和路由，不调用模型、信号源或采集器。
package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/service"
	"drop/api/internal/testutil"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type verificationSignalsFixture struct {
	db                                    *gorm.DB
	server                                *Server
	router                                *gin.Engine
	reader                                domain.User
	owner                                 domain.User
	baseline, candidate                   domain.Task
	baselineDiagnosis, candidateDiagnosis service.DiagnosisRun
	verification                          service.VerificationRun
	groupID, agentID, token, marker       string
}

func newVerificationSignalsFixture(t *testing.T, baselineSignal, candidateSignal bool) verificationSignalsFixture {
	t.Helper()
	db := testutil.Postgres(t)
	repo := repository.New(db)
	ctx := context.Background()
	owner := domain.User{ID: "verification-owner", Username: "verification-owner", Role: "operator"}
	reader := domain.User{ID: "verification-reader", Username: "verification-reader", Role: "operator"}
	for _, u := range []domain.User{owner, reader} {
		if err := db.Create(&u).Error; err != nil {
			t.Fatal(err)
		}
	}
	aid, gid := "verification-signal-agent", "verification-signal-group"
	if err := db.Create(&domain.Agent{ID: aid, Hostname: "explicit-imported-agent", Version: "fixture", Platform: "linux", Status: "online", Capabilities: json.RawMessage(`["PERF_CPU"]`), Labels: json.RawMessage(`{}`), Resources: json.RawMessage(`{}`)}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&domain.Group{ID: gid, Name: "明确合成数据源授权组"}).Error; err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(map[string]any{"schema_version": 1, "services": []service.TelemetryService{{ID: "verification-source", Label: "合成只读信号源", AgentID: aid, GroupID: &gid, Connectors: map[string]service.TelemetryConnector{"metrics": {Kind: "drop-adapter", BaseURL: "http://127.0.0.1:1", AllowHTTP: true}}}}})
	registry, err := service.NewObservabilityRegistry(raw)
	if err != nil {
		t.Fatal(err)
	}
	create := func(name string, signal bool) (domain.Task, service.DiagnosisRun) {
		t.Helper()
		task, attempt, _, err := repo.CreateTask(ctx, domain.Task{Name: "明确导入复测授权夹具 " + name, Kind: "PERF_CPU", OwnerID: owner.ID, AgentID: &aid, Visibility: "private", Parameters: json.RawMessage(`{"pid":7,"duration_seconds":5,"frequency_hz":99,"scope":"process"}`), CollectionStatus: "COLLECTED", AnalysisStatus: "SUCCESS", IdempotencyKey: "verification-" + name, RequestID: "imported-fixture"}, false, domain.Audit{Action: "imported_fixture", ResourceType: "task", Detail: json.RawMessage(`{}`)})
		if err != nil {
			t.Fatal(err)
		}
		var jobID int64
		if err := db.Raw("INSERT INTO analysis_jobs(task_id,attempt_id,pipeline,status,attempt,result) VALUES(?,?,'folded_v1','success',1,?::jsonb) RETURNING id", task.ID, attempt.ID, `{"kind":"PERF_CPU","total_samples":1000,"summary":{"unit":"samples"}}`).Scan(&jobID).Error; err != nil {
			t.Fatal(err)
		}
		run := service.DiagnosisRun{TaskID: task.ID, AttemptID: attempt.ID, AnalysisJobID: jobID, AnalysisRun: 1, RequestedBy: owner.ID, Goal: "明确导入的复测报告权限回归", Mode: "evidence", KnowledgeMode: "off", ReportProtocol: 1, InputDigest: "explicit-imported-fixture", RequestKey: "verification-diagnosis-" + name, Status: "success", Trace: json.RawMessage(`[]`), Report: json.RawMessage(`{"summary":"明确导入的合成诊断"}`)}
		if signal {
			run.TelemetryServiceID = "verification-source"
			run.Trace, _ = json.Marshal([]service.DiagnosisStep{{EvidenceID: "E1", Tool: "read_profile_summary", Observation: mustVerificationJSON(map[string]any{"observability_revision": registry.Digest})}, {EvidenceID: "E2", Tool: "query_business_signals", Observation: json.RawMessage(`{"available":true,"service_ref":"S1","signal":"metrics"}`)}})
		}
		if err := db.Create(&run).Error; err != nil {
			t.Fatal(err)
		}
		if err := db.Exec("INSERT INTO task_shares(task_id,user_id,permission,created_by) VALUES(?,?,'operate',?)", task.ID, reader.ID, owner.ID).Error; err != nil {
			t.Fatal(err)
		}
		return task, run
	}
	baseline, baseRun := create("baseline", baselineSignal)
	candidate, candidateRun := create("candidate", candidateSignal)
	marker := "EXPLICIT_IMPORTED_COPIED_SIGNAL_REPORT"
	v := service.VerificationRun{DiagnosisID: baseRun.ID, BaselineTaskID: baseline.ID, CandidateTaskID: candidate.ID, RequestedBy: owner.ID, CandidateDiagnosisID: &candidateRun.ID, Status: "success", Report: mustVerificationJSON(map[string]any{"candidate_report": map[string]any{"summary": marker}})}
	if err := db.Create(&v).Error; err != nil {
		t.Fatal(err)
	}
	token := strings.Repeat("e", 64)
	if err := repo.NewSession(ctx, repository.Session{TokenHash: repository.Digest(token), UserID: reader.ID, CSRFToken: strings.Repeat("f", 64), ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatal(err)
	}
	server := &Server{Repo: repo, Tasks: &service.Tasks{Repo: repo}, Suggestions: &service.Suggestions{Repo: repo, Observability: registry}}
	return verificationSignalsFixture{db, server, server.Router(), reader, owner, baseline, candidate, baseRun, candidateRun, v, gid, aid, token, marker}
}

func mustVerificationJSON(value any) json.RawMessage { raw, _ := json.Marshal(value); return raw }

func (f verificationSignalsFixture) grantSource(t *testing.T) {
	t.Helper()
	if err := f.db.Exec("INSERT INTO group_members(group_id,user_id,role) VALUES(?,?,'operator') ON CONFLICT(group_id,user_id) DO UPDATE SET role=excluded.role", f.groupID, f.reader.ID).Error; err != nil {
		t.Fatal(err)
	}
	if err := f.db.Exec("INSERT INTO group_agents(group_id,agent_id,permission) VALUES(?,?,'read') ON CONFLICT(group_id,agent_id) DO UPDATE SET permission=excluded.permission", f.groupID, f.agentID).Error; err != nil {
		t.Fatal(err)
	}
}

func (f verificationSignalsFixture) assertVisible(t *testing.T, want bool) {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, fmt.Sprintf("/api/v1/tasks/%d/verifications", f.baseline.ID), nil)
	req.AddCookie(&http.Cookie{Name: "drop_session", Value: f.token})
	w := httptest.NewRecorder()
	f.router.ServeHTTP(w, req)
	var response struct {
		Data struct {
			Items []service.VerificationRun `json:"items"`
			Total int64                     `json:"total"`
		} `json:"data"`
	}
	if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &response) != nil {
		t.Fatalf("复测列表请求失败：status=%d", w.Code)
	}
	if want {
		if len(response.Data.Items) != 1 || response.Data.Total != 1 || !strings.Contains(w.Body.String(), f.marker) {
			t.Fatal("授权后的诊断副本不可读")
		}
	} else if len(response.Data.Items) != 0 || response.Data.Total != 0 || strings.Contains(w.Body.String(), f.marker) {
		t.Fatal("未授权诊断副本通过复测列表泄漏")
	}
}

func TestPostgresVerificationSignalsEveryCopiedSourceRequiresCurrentAuthorization(t *testing.T) {
	for _, signal := range []struct {
		name                string
		baseline, candidate bool
	}{{"baseline-only", true, false}, {"candidate-only", false, true}, {"both", true, true}} {
		t.Run(signal.name, func(t *testing.T) {
			f := newVerificationSignalsFixture(t, signal.baseline, signal.candidate)
			f.assertVisible(t, false) // 两个profile已有operate共享，不能据此读取独立业务信号。
			f.grantSource(t)
			f.assertVisible(t, true)
			if err := f.db.Exec("DELETE FROM group_members WHERE group_id=? AND user_id=?", f.groupID, f.reader.ID).Error; err != nil {
				t.Fatal(err)
			}
			f.assertVisible(t, false)
			f.grantSource(t)
			f.assertVisible(t, true)
			if err := f.db.Exec("DELETE FROM group_agents WHERE group_id=? AND agent_id=?", f.groupID, f.agentID).Error; err != nil {
				t.Fatal(err)
			}
			f.assertVisible(t, false)
			f.grantSource(t)
			original := f.server.Suggestions.Observability.Digest
			f.server.Suggestions.Observability.Digest = strings.Repeat("b", 64)
			f.assertVisible(t, false)
			f.server.Suggestions.Observability.Digest = original
			f.assertVisible(t, true)
			if err := f.db.Model(&domain.User{}).Where("id=?", f.reader.ID).Update("role", "viewer").Error; err != nil {
				t.Fatal(err)
			}
			f.assertVisible(t, false)
		})
	}
}

func TestPostgresVerificationSignalsNilSuggestionsFailsClosedAndKeepsLegacy(t *testing.T) {
	for _, source := range []struct {
		name   string
		signal bool
	}{{"signal", true}, {"legacy", false}} {
		t.Run(source.name, func(t *testing.T) {
			f := newVerificationSignalsFixture(t, false, source.signal)
			f.grantSource(t)
			f.server.Suggestions = nil
			f.assertVisible(t, !source.signal)
			if !source.signal {
				// 旧复测可没有候选诊断，但真实基线仍须可核实；不借兼容路径忽略非空来源ID。
				if err := f.db.Model(&service.VerificationRun{}).Where("id=?", f.verification.ID).Update("candidate_diagnosis_id", nil).Error; err != nil {
					t.Fatal(err)
				}
				f.assertVisible(t, true)
			}
		})
	}
}

func TestPostgresVerificationSignalsRejectsMismatchedSourceAndKeepsTaskWriteChecks(t *testing.T) {
	f := newVerificationSignalsFixture(t, true, true)
	f.grantSource(t)
	f.assertVisible(t, true)
	if err := f.db.Exec("DELETE FROM task_shares WHERE task_id=? AND user_id=?", f.candidate.ID, f.reader.ID).Error; err != nil {
		t.Fatal(err)
	}
	f.assertVisible(t, false)
	if err := f.db.Exec("INSERT INTO task_shares(task_id,user_id,permission,created_by) VALUES(?,?,'read',?)", f.candidate.ID, f.reader.ID, f.owner.ID).Error; err != nil {
		t.Fatal(err)
	}
	f.assertVisible(t, false)
	if err := f.db.Exec("UPDATE task_shares SET permission='operate' WHERE task_id=? AND user_id=?", f.candidate.ID, f.reader.ID).Error; err != nil {
		t.Fatal(err)
	}
	f.assertVisible(t, true)
	if err := f.db.Model(&service.VerificationRun{}).Where("id=?", f.verification.ID).Update("candidate_diagnosis_id", f.baselineDiagnosis.ID).Error; err != nil {
		t.Fatal(err)
	}
	f.assertVisible(t, false)
}
