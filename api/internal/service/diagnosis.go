package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
)

// DiagnosisRun is separate from collection/analysis state: a diagnosis can fail
// without changing the measured result. Trace contains tool observations only,
// never hidden chain-of-thought, raw profiles or provider responses.
type DiagnosisRun struct {
	ID             int64           `json:"id,string"`
	TaskID         int64           `json:"task_id,string"`
	AttemptID      int64           `json:"attempt_id,string"`
	AnalysisJobID  int64           `json:"analysis_job_id,string"`
	AnalysisRun    int             `json:"analysis_run"`
	RequestedBy    string          `json:"requested_by"`
	Goal           string          `json:"goal"`
	Mode           string          `json:"mode"`
	InputDigest    string          `json:"input_digest"`
	RequestKey     string          `json:"-"`
	Status         string          `json:"status"`
	LeaseOwner     *string         `json:"-"`
	LeaseExpiresAt *time.Time      `json:"-"`
	Attempts       int             `json:"-"`
	Trace          json.RawMessage `json:"trace"`
	Report         json.RawMessage `json:"report"`
	ErrorCode      *string         `json:"error_code,omitempty"`
	CreatedAt      time.Time       `json:"created_at"`
	UpdatedAt      time.Time       `json:"updated_at"`
}
type DiagnosisInput struct {
	Goal    string `json:"goal"`
	Mode    string `json:"mode"`
	Consent bool   `json:"consent"`
}

func (s *Suggestions) RequestDiagnosis(ctx context.Context, u domain.User, id int64, in DiagnosisInput, request, key string) (DiagnosisRun, error) {
	var run DiagnosisRun
	if err := ValidateKey(key); err != nil {
		return run, err
	}
	in.Goal = strings.TrimSpace(in.Goal)
	if !utf8.ValidString(in.Goal) || utf8.RuneCountInString(in.Goal) < 1 || utf8.RuneCountInString(in.Goal) > 600 || strings.IndexFunc(in.Goal, func(r rune) bool { return unicode.IsControl(r) && r != '\n' && r != '\t' }) >= 0 || (in.Mode != "evidence" && in.Mode != "model") {
		return run, domain.Fail("INVALID_PARAMETERS", "诊断问题须为1–600字，模式为evidence或model")
	}
	if in.Mode == "model" && !in.Consent {
		return run, domain.Fail("CONSENT_REQUIRED", "仅将问题和脱敏统计发送模型，请先确认问题不含敏感信息")
	}
	if in.Mode == "model" && !s.AgentAvailable() {
		return run, domain.Fail("AI_UNAVAILABLE", "未配置外部模型，可使用本地证据检查")
	}
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		r := repository.New(tx)
		// Serializes per-user admission; concurrent requests cannot bypass the cap.
		if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,417626))", u.ID).Error; err != nil {
			return err
		}
		var locked domain.User
		if err := tx.Where("id=?", u.ID).First(&locked).Error; err != nil {
			return err
		}
		t, err := r.AuthorizeTaskWrite(ctx, locked, id)
		if err != nil {
			return err
		}
		if err := tx.Raw("SELECT * FROM tasks WHERE id=? FOR UPDATE", id).Scan(&t).Error; err != nil {
			return err
		}
		if t.AnalysisStatus != "SUCCESS" || t.CollectionStatus != "COLLECTED" {
			return domain.Fail("RESULT_NOT_READY", "仅完整成功结果可用于诊断")
		}
		attempt, err := r.LatestAttempt(ctx, id)
		if err != nil {
			return err
		}
		raw, err := r.Result(ctx, id)
		if err != nil {
			return err
		}
		job, err := diagnosisSource(ctx, tx, id, attempt.ID)
		if err != nil {
			return err
		}
		digest := repository.Digest(string(raw) + "\x00" + in.Mode + "\x00" + in.Goal)
		err = tx.Where("requested_by=? AND request_key=?", u.ID, key).First(&run).Error
		if err == nil {
			if run.TaskID != id || run.AttemptID != attempt.ID || run.InputDigest != digest || run.AnalysisJobID != job.ID || run.AnalysisRun != job.Attempt {
				return domain.Fail("IDEMPOTENCY_CONFLICT", "幂等键已用于不同诊断")
			}
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		var count int64
		if err := tx.Table("diagnosis_runs").Where("requested_by=? AND status IN ('pending','running')", u.ID).Count(&count).Error; err != nil {
			return err
		}
		if count >= 3 {
			return domain.Fail("DIAGNOSIS_BUSY", "最多同时运行3个诊断，请等待或取消已有诊断")
		}
		run = DiagnosisRun{TaskID: id, AttemptID: attempt.ID, AnalysisJobID: job.ID, AnalysisRun: job.Attempt, RequestedBy: u.ID, Goal: in.Goal, Mode: in.Mode, RequestKey: key, InputDigest: digest, Status: "pending", Trace: json.RawMessage("[]")}
		if err := tx.Create(&run).Error; err != nil {
			return err
		}
		event := domain.EventFor(t, &attempt.ID, "DIAGNOSIS_QUEUED", map[string]any{"diagnosis_id": fmt.Sprint(run.ID), "mode": in.Mode})
		if err := tx.Create(&event).Error; err != nil {
			return err
		}
		return tx.Create(ptrAuditDiagnosis(Audit(u, "diagnosis.request", "task", fmt.Sprint(id), request))).Error
	})
	return run, err
}
func ptrAuditDiagnosis(a domain.Audit) *domain.Audit { return &a }

type diagnosisAnalysisSource struct {
	ID      int64
	Attempt int
}

func diagnosisSource(ctx context.Context, tx *gorm.DB, taskID, attemptID int64) (diagnosisAnalysisSource, error) {
	var source diagnosisAnalysisSource
	err := tx.WithContext(ctx).Raw("SELECT id,attempt FROM analysis_jobs WHERE task_id=? AND attempt_id=? AND status='success'", taskID, attemptID).Scan(&source).Error
	if err == nil && source.ID == 0 {
		err = domain.Fail("RESULT_NOT_READY", "没有完整分析结果")
	}
	return source, err
}
func (s *Suggestions) ValidateDiagnosisSource(ctx context.Context, run DiagnosisRun) error {
	job, err := diagnosisSource(ctx, s.Repo.Context(ctx), run.TaskID, run.AttemptID)
	if err != nil {
		return err
	}
	if job.ID != run.AnalysisJobID || job.Attempt != run.AnalysisRun {
		return domain.Fail("STALE_ATTEMPT", "分析执行已更新")
	}
	raw, err := s.Repo.Result(ctx, run.TaskID)
	if err != nil {
		return err
	}
	if repository.Digest(string(raw)+"\x00"+run.Mode+"\x00"+run.Goal) != run.InputDigest {
		return domain.Fail("INPUT_CHANGED", "证据已改变")
	}
	return nil
}

func (s *Suggestions) RunDiagnosis(ctx context.Context) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			_ = s.processDiagnosis(ctx)
		}
	}
}

func (s *Suggestions) processDiagnosis(ctx context.Context) error {
	owner := RandomToken()
	var run DiagnosisRun
	// Crash recovery is explicit. A bounded read-only run is restarted at most 3
	// times; lease ownership fences every trace and final commit.
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var expired []DiagnosisRun
		if err := tx.Raw("UPDATE diagnosis_runs SET status='failed',error_code='DIAGNOSIS_RETRY_EXHAUSTED',lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE status='running' AND lease_expires_at<now() AND attempts>=3 RETURNING *").Scan(&expired).Error; err != nil {
			return err
		}
		for _, r := range expired {
			ev := domain.EventFor(domain.Task{ID: r.TaskID}, &r.AttemptID, "DIAGNOSIS_FAILED", map[string]any{"diagnosis_id": fmt.Sprint(r.ID), "error_code": "DIAGNOSIS_RETRY_EXHAUSTED"})
			if err := tx.Create(&ev).Error; err != nil {
				return err
			}
		}
		return tx.Raw(`UPDATE diagnosis_runs SET status='running',attempts=attempts+1,lease_owner=?,lease_expires_at=now()+interval '180 seconds',trace='[]',report=NULL,updated_at=now() WHERE id=(SELECT id FROM diagnosis_runs WHERE (status='pending' OR (status='running' AND lease_expires_at<now())) AND attempts<3 ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *`, owner).Scan(&run).Error
	})
	if err != nil || run.ID == 0 {
		return err
	}
	work, cancel := context.WithTimeout(ctx, 150*time.Second)
	defer cancel()
	trace := []DiagnosisStep{}
	report, err := s.diagnose(work, run, owner, &trace)
	// A timed-out upstream must still commit a durable failure, not wait for lease
	// recovery. Finalization gets a separate, bounded context.
	finish, stop := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer stop()
	return s.finishDiagnosis(finish, run, owner, trace, report, err)
}

func (s *Suggestions) diagnosisFence(ctx context.Context, tx *gorm.DB, run DiagnosisRun, owner string) (domain.Task, error) {
	var boundVerification VerificationRun
	if err := tx.Raw("SELECT * FROM verification_runs WHERE candidate_diagnosis_id=? FOR UPDATE", run.ID).Scan(&boundVerification).Error; err != nil {
		return domain.Task{}, err
	}
	if boundVerification.ID > 0 {
		var lockedIDs []int64
		if err := tx.Raw("SELECT id FROM tasks WHERE id IN (?,?) ORDER BY id FOR UPDATE", boundVerification.BaselineTaskID, boundVerification.CandidateTaskID).Scan(&lockedIDs).Error; err != nil {
			return domain.Task{}, err
		}
	}
	r := repository.New(tx)
	u, err := r.User(ctx, run.RequestedBy)
	if err != nil {
		return domain.Task{}, domain.Fail("AUTHORIZATION_REVOKED", "诊断授权已失效")
	}
	t, err := r.AuthorizeTaskWrite(ctx, u, run.TaskID)
	if err != nil {
		return t, domain.Fail("AUTHORIZATION_REVOKED", "诊断授权已撤销")
	}
	if err := tx.Raw("SELECT * FROM tasks WHERE id=? FOR UPDATE", run.TaskID).Scan(&t).Error; err != nil {
		return t, err
	}
	a, err := r.LatestAttempt(ctx, t.ID)
	if err != nil {
		return t, err
	}
	if a.ID != run.AttemptID || t.AnalysisStatus != "SUCCESS" || t.CollectionStatus != "COLLECTED" {
		return t, domain.Fail("STALE_ATTEMPT", "采集或分析已更新")
	}
	raw, err := r.Result(ctx, t.ID)
	if err != nil {
		return t, err
	}
	job, err := diagnosisSource(ctx, tx, t.ID, run.AttemptID)
	if err != nil {
		return t, err
	}
	if job.ID != run.AnalysisJobID || job.Attempt != run.AnalysisRun {
		return t, domain.Fail("STALE_ATTEMPT", "分析执行版本已改变")
	}
	if repository.Digest(string(raw)+"\x00"+run.Mode+"\x00"+run.Goal) != run.InputDigest {
		return t, domain.Fail("INPUT_CHANGED", "证据已改变")
	}
	var verification VerificationRun
	if err := tx.Where("candidate_diagnosis_id=?", run.ID).Limit(1).Find(&verification).Error; err != nil {
		return t, err
	}
	if verification.ID > 0 {
		if verification.Status == "canceled" {
			return t, domain.Fail("DIAGNOSIS_CANCELED", "复测闭环已取消")
		}
		if _, _, err := verificationBaseline(ctx, tx, verification); err != nil {
			return t, err
		}
	}
	var n int64
	if err := tx.Raw("SELECT id FROM diagnosis_runs WHERE id=? AND status='running' AND lease_owner=? AND lease_expires_at>now() FOR UPDATE", run.ID, owner).Scan(&n).Error; err != nil {
		return t, err
	}
	if n == 0 {
		return t, domain.Fail("STALE_LEASE", "诊断已取消或租约已失效")
	}
	return t, nil
}

func (s *Suggestions) persistDiagnosisStep(ctx context.Context, run DiagnosisRun, owner string, trace []DiagnosisStep) error {
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		t, err := s.diagnosisFence(ctx, tx, run, owner)
		if err != nil {
			return err
		}
		b, _ := json.Marshal(trace)
		if err := tx.Table("diagnosis_runs").Where("id=?", run.ID).Updates(map[string]any{"trace": string(b), "updated_at": time.Now().UTC()}).Error; err != nil {
			return err
		}
		ev := domain.EventFor(t, &run.AttemptID, "DIAGNOSIS_TOOL_COMPLETED", map[string]any{"diagnosis_id": fmt.Sprint(run.ID), "step": len(trace), "tool": trace[len(trace)-1].Tool})
		return tx.Create(&ev).Error
	})
}
func (s *Suggestions) finishDiagnosis(ctx context.Context, run DiagnosisRun, owner string, trace []DiagnosisStep, report *DiagnosisReport, workErr error) error {
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		_, fenceErr := s.diagnosisFence(ctx, tx, run, owner)
		if fenceErr != nil {
			workErr = fenceErr
		}
		state, code := "success", ""
		if workErr != nil {
			state = "failed"
			code = "DIAGNOSIS_FAILED"
			var e *domain.Error
			if errors.As(workErr, &e) {
				code = e.Code
			}
			if errors.Is(workErr, context.DeadlineExceeded) {
				code = "DIAGNOSIS_TIMEOUT"
			}
		}
		b, _ := json.Marshal(trace)
		var result any
		if state == "success" {
			rb, _ := json.Marshal(report)
			result = string(rb)
		}
		res := tx.Exec("UPDATE diagnosis_runs SET status=?,trace=?::jsonb,report=?::jsonb,error_code=NULLIF(?,''),lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE id=? AND status='running' AND lease_owner=? AND lease_expires_at>now()", state, string(b), result, code, run.ID, owner)
		if res.Error != nil {
			return res.Error
		}
		if res.RowsAffected == 0 {
			return nil
		}
		typ := "DIAGNOSIS_COMPLETED"
		if state != "success" {
			typ = "DIAGNOSIS_FAILED"
		}
		ev := domain.EventFor(domain.Task{ID: run.TaskID}, &run.AttemptID, typ, map[string]any{"diagnosis_id": fmt.Sprint(run.ID), "error_code": code})
		return tx.Create(&ev).Error
	})
}

func (s *Suggestions) CancelDiagnosis(ctx context.Context, u domain.User, taskID, runID int64, request string) error {
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		r := repository.New(tx)
		if _, err := r.AuthorizeTaskWrite(ctx, u, taskID); err != nil {
			return err
		}
		var t domain.Task
		if err := tx.Raw("SELECT * FROM tasks WHERE id=? FOR UPDATE", taskID).Scan(&t).Error; err != nil {
			return err
		}
		res := tx.Exec("UPDATE diagnosis_runs SET status='failed',error_code='DIAGNOSIS_CANCELED',lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE id=? AND task_id=? AND status IN ('pending','running')", runID, taskID)
		if res.Error != nil {
			return res.Error
		}
		if res.RowsAffected == 0 {
			return domain.Fail("DIAGNOSIS_NOT_ACTIVE", "诊断已结束或不存在")
		}
		ev := domain.EventFor(t, nil, "DIAGNOSIS_FAILED", map[string]any{"diagnosis_id": fmt.Sprint(runID), "error_code": "DIAGNOSIS_CANCELED"})
		if err := tx.Create(&ev).Error; err != nil {
			return err
		}
		return tx.Create(ptrAuditDiagnosis(Audit(u, "diagnosis.cancel", "task", fmt.Sprint(taskID), request))).Error
	})
}
