// Package service：推进基线采集→候选采集→新诊断→比较的持久化闭环。
// 比较先检查目标、单位和来源身份，再计算比例差异；重复采样只能支持可复现性，因果结论需要独立干预。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
)

// VerificationRun 持久化复测编排，使 API 重启后仍可继续。
// 候选结果会启动全新的诊断工具循环，不能复用基线报告；重复采样只检验可复现性，
// 根因因果确认仍需要独立的受控修改与对照测试。
type VerificationRun struct {
	ID                   int64           `json:"id,string"`
	DiagnosisID          int64           `json:"diagnosis_id,string"`
	BaselineTaskID       int64           `json:"baseline_task_id,string"`
	CandidateTaskID      int64           `json:"candidate_task_id,string"`
	RequestedBy          string          `json:"requested_by"`
	CandidateDiagnosisID *int64          `json:"candidate_diagnosis_id,string,omitempty"`
	Status               string          `json:"status"`
	Report               json.RawMessage `json:"report"`
	ErrorCode            *string         `json:"error_code,omitempty"`
	CreatedAt            time.Time       `json:"created_at"`
	UpdatedAt            time.Time       `json:"updated_at"`
}

type ProfileDifference struct {
	Symbol        string  `json:"symbol"`
	BaselineSelf  float64 `json:"baseline_self_percent"`
	CandidateSelf float64 `json:"candidate_self_percent"`
	Delta         float64 `json:"delta_percentage_points"`
}
type ProfileComparison struct {
	Comparable      bool                `json:"comparable"`
	Kind            string              `json:"kind"`
	Unit            string              `json:"unit"`
	BaselineWeight  int64               `json:"baseline_weight"`
	CandidateWeight int64               `json:"candidate_weight"`
	Differences     []ProfileDifference `json:"differences"`
	Outcome         string              `json:"outcome"`
	RootCauseStatus string              `json:"root_cause_status"`
	Limitations     []string            `json:"limitations"`
}

// compareProfiles 先核验目标、采集种类、单位和输入身份，再计算相同比较口径下的热点比例差异。
// 样本量、不可比原因和未检验的因果状态一并返回，不能把比例下降直接称为耗时收益。
func compareProfiles(baseline, candidate json.RawMessage, a, b domain.Task) (ProfileComparison, error) {
	out := ProfileComparison{Kind: a.Kind, RootCauseStatus: "not_tested", Outcome: "insufficient_evidence", Differences: []ProfileDifference{}, Limitations: []string{"重复采样只检验现象复现；未实施受控代码或配置干预，不能确认因果根因。", "百分比差值不是请求延迟或CPU利用率变化；需同一负载并记录业务指标。"}}
	type profile struct {
		Total   int64 `json:"total_samples"`
		Summary struct {
			Unit string `json:"unit"`
		} `json:"summary"`
		Top []struct {
			Name string  `json:"name"`
			Self float64 `json:"self_percent"`
		} `json:"top_functions"`
	}
	var x, y profile
	if len(baseline) > 8<<20 || len(candidate) > 8<<20 || json.Unmarshal(baseline, &x) != nil || json.Unmarshal(candidate, &y) != nil {
		return out, domain.Fail("INVALID_EVIDENCE", "对比结果超限或格式无效")
	}
	out.Unit = x.Summary.Unit
	out.BaselineWeight = x.Total
	out.CandidateWeight = y.Total
	var p, q domain.Parameters
	if json.Unmarshal(a.Parameters, &p) != nil || json.Unmarshal(b.Parameters, &q) != nil {
		return out, domain.Fail("INVALID_EVIDENCE", "目标参数无法读取")
	}
	pt, _ := json.Marshal(p.Target)
	qt, _ := json.Marshal(q.Target)
	if a.Kind != b.Kind || a.AgentID == nil || b.AgentID == nil || *a.AgentID != *b.AgentID || p.PID != q.PID || p.Scope != q.Scope || string(pt) != string(qt) || p.Source == "import" || q.Source == "import" || out.Unit == "" || out.Unit == "unknown" || out.Unit != y.Summary.Unit {
		out.Limitations = append(out.Limitations, "采集器、来源、目标身份或测量单位不一致/未知，拒绝自动比较。")
		return out, nil
	}
	out.Comparable = true
	values := map[string]*ProfileDifference{}
	for side, rows := range [][]struct {
		Name string  `json:"name"`
		Self float64 `json:"self_percent"`
	}{x.Top, y.Top} {
		if len(rows) > 10000 {
			return out, domain.Fail("INVALID_EVIDENCE", "热点数量超限")
		}
		for _, f := range rows {
			if math.IsNaN(f.Self) || math.IsInf(f.Self, 0) || f.Self < 0 || f.Self > 100 {
				return out, domain.Fail("INVALID_EVIDENCE", "热点比例无效")
			}
			key := "symbol_" + repository.Digest(f.Name)[:12]
			v := values[key]
			if v == nil {
				v = &ProfileDifference{Symbol: key}
				values[key] = v
			}
			if side == 0 {
				v.BaselineSelf = f.Self
			} else {
				v.CandidateSelf = f.Self
			}
		}
	}
	for _, v := range values {
		v.Delta = v.CandidateSelf - v.BaselineSelf
		out.Differences = append(out.Differences, *v)
	}
	sort.Slice(out.Differences, func(i, j int) bool {
		a, b := math.Abs(out.Differences[i].Delta), math.Abs(out.Differences[j].Delta)
		if a == b {
			return out.Differences[i].Symbol < out.Differences[j].Symbol
		}
		return a > b
	})
	if len(out.Differences) > 10 {
		out.Differences = out.Differences[:10]
	}
	// 不能把任意设定的百分比容差解释为因果关系，重复采样的差异也不自动证明修复有效。
	if out.Unit == "samples" && x.Total >= 1000 && y.Total >= 1000 {
		out.Outcome = "comparison_ready"
	} else {
		out.Limitations = append(out.Limitations, "样本数不足1000或单位不是采样计数；只展示描述差异，不给统计显著性结论。")
	}
	out.Limitations = append(out.Limitations, "Top表未出现的符号按0显示仅指已返回热点表，不证明实际没有执行。")
	return out, nil
}

func verificationBaseline(ctx context.Context, tx *gorm.DB, v VerificationRun) (domain.Task, DiagnosisRun, error) {
	var lockedIDs []int64
	if err := tx.Raw("SELECT id FROM tasks WHERE id IN (?,?) ORDER BY id FOR UPDATE", v.BaselineTaskID, v.CandidateTaskID).Scan(&lockedIDs).Error; err != nil {
		return domain.Task{}, DiagnosisRun{}, err
	}
	r := repository.New(tx)
	u, err := r.User(ctx, v.RequestedBy)
	if err != nil {
		return domain.Task{}, DiagnosisRun{}, err
	}
	t, err := r.AuthorizeTaskWrite(ctx, u, v.BaselineTaskID)
	if err != nil {
		return t, DiagnosisRun{}, domain.Fail("AUTHORIZATION_REVOKED", "原任务授权已失效")
	}
	var run DiagnosisRun
	if err = tx.Where("id=? AND task_id=? AND status='success'", v.DiagnosisID, t.ID).First(&run).Error; err != nil {
		return t, run, domain.Fail("STALE_ATTEMPT", "原诊断不可用")
	}
	a, err := r.LatestAttempt(ctx, t.ID)
	if err != nil {
		return t, run, err
	}
	if a.ID != run.AttemptID || t.CollectionStatus != "COLLECTED" || t.AnalysisStatus != "SUCCESS" {
		return t, run, domain.Fail("STALE_ATTEMPT", "原采集版本已更新")
	}
	s := Suggestions{Repo: r}
	return t, run, s.ValidateDiagnosisSource(ctx, run)
}

func verificationComparison(ctx context.Context, tx *gorm.DB, v VerificationRun) (ProfileComparison, error) {
	a, _, err := verificationBaseline(ctx, tx, v)
	if err != nil {
		return ProfileComparison{}, err
	}
	r := repository.New(tx)
	u, err := r.User(ctx, v.RequestedBy)
	if err != nil {
		return ProfileComparison{}, err
	}
	b, err := r.AuthorizeTaskWrite(ctx, u, v.CandidateTaskID)
	if err != nil {
		return ProfileComparison{}, domain.Fail("AUTHORIZATION_REVOKED", "复测授权已失效")
	}
	if b.CollectionStatus != "COLLECTED" || b.AnalysisStatus != "SUCCESS" {
		return ProfileComparison{}, domain.Fail("RESULT_NOT_READY", "复测分析未完成")
	}
	x, err := r.Result(ctx, a.ID)
	if err != nil {
		return ProfileComparison{}, err
	}
	y, err := r.Result(ctx, b.ID)
	if err != nil {
		return ProfileComparison{}, err
	}
	return compareProfiles(x, y, a, b)
}

func (s *Suggestions) RunVerifications(ctx context.Context) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			_ = s.processVerification(ctx)
		}
	}
}

// processVerification 按与普通诊断一致的锁顺序领取复测工作，逐步读取已持久化的采集和诊断事实。
// 每步可在重启后继续，不能通过长事务等待采集器或模型完成。
func (s *Suggestions) processVerification(ctx context.Context) error {
	var rows []VerificationRun
	if err := s.Repo.Context(ctx).Where("status IN ('waiting','diagnosing','comparing')").Order("id").Limit(8).Find(&rows).Error; err != nil {
		return err
	}
	for _, candidate := range rows {
		if err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
			// 先使用与普通诊断相同的准入锁顺序，再锁任务，避免两条工作流相互等待。
			// 被外键引用的用户行不能替代专门的配额互斥机制。
			if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,417626))", candidate.RequestedBy).Error; err != nil {
				return err
			}
			var v VerificationRun
			if err := tx.Raw("SELECT * FROM verification_runs WHERE id=? AND status IN ('waiting','diagnosing','comparing') FOR UPDATE SKIP LOCKED", candidate.ID).Scan(&v).Error; err != nil {
				return err
			}
			if v.ID == 0 {
				return nil
			}
			err := s.advanceVerification(ctx, tx, &v)
			if err != nil {
				v.Status = "failed"
				code := "VERIFICATION_FAILED"
				if e, ok := err.(*domain.Error); ok {
					code = e.Code
				}
				v.ErrorCode = &code
			}
			if err := tx.Model(&VerificationRun{}).Where("id=?", v.ID).Updates(map[string]any{"status": v.Status, "candidate_diagnosis_id": v.CandidateDiagnosisID, "report": v.Report, "error_code": v.ErrorCode, "updated_at": time.Now().UTC()}).Error; err != nil {
				return err
			}
			if v.Status == "success" || v.Status == "failed" {
				ev := domain.EventFor(domain.Task{ID: v.BaselineTaskID}, nil, "DIAGNOSIS_VERIFICATION_COMPLETED", map[string]any{"verification_id": fmt.Sprint(v.ID), "status": v.Status, "candidate_task_id": fmt.Sprint(v.CandidateTaskID)})
				if err := tx.Create(&ev).Error; err != nil {
					return err
				}
			}
			return nil
		}); err != nil {
			return err
		}
	}
	return nil
}

// advanceVerification 只根据当前基线、候选任务及各自诊断状态推进复测；来源身份改变或权限撤销时停止关联链，防止混用不同时期证据。
func (s *Suggestions) advanceVerification(ctx context.Context, tx *gorm.DB, v *VerificationRun) error {
	if time.Since(v.CreatedAt) > 15*time.Minute {
		return domain.Fail("VERIFICATION_TIMEOUT", "复测闭环超过15分钟预算")
	}
	_, source, err := verificationBaseline(ctx, tx, *v)
	if err != nil {
		return err
	}
	r := repository.New(tx)
	u, err := r.User(ctx, v.RequestedBy)
	if err != nil {
		return err
	}
	t, err := r.AuthorizeTaskWrite(ctx, u, v.CandidateTaskID)
	if err != nil {
		return domain.Fail("AUTHORIZATION_REVOKED", "复测权限已撤销")
	}
	if t.CollectionStatus == "FAILED" || t.CollectionStatus == "CANCELED" || t.AnalysisStatus == "FAILED" {
		return domain.Fail("VERIFICATION_COLLECTION_FAILED", "复测任务失败或取消")
	}
	if v.Status == "waiting" {
		if t.CollectionStatus != "COLLECTED" || t.AnalysisStatus != "SUCCESS" {
			return nil
		}
		scoped := *s
		scoped.Repo = r
		run, err := scoped.RequestDiagnosis(ctx, u, t.ID, DiagnosisInput{Goal: "复测后重新检查热点和质量，调用compare_profiles对比原采集，评估原假设是否仍受支持；区分现象复现和因果根因。", Mode: source.Mode, Consent: source.Mode == "model"}, "verification-worker", fmt.Sprintf("verification:%d:diagnosis", v.ID))
		if err != nil {
			return err
		}
		v.CandidateDiagnosisID = &run.ID
		v.Status = "diagnosing"
		return nil
	}
	if v.CandidateDiagnosisID == nil {
		return domain.Fail("INVALID_STATE", "复测缺少诊断身份")
	}
	var run DiagnosisRun
	if err := tx.Where("id=?", *v.CandidateDiagnosisID).First(&run).Error; err != nil {
		return err
	}
	if run.Status == "failed" {
		return domain.Fail("VERIFICATION_DIAGNOSIS_FAILED", "复测诊断失败，请检查记录")
	}
	if run.Status != "success" {
		return nil
	}
	if err := (&Suggestions{Repo: r}).ValidateDiagnosisSource(ctx, run); err != nil {
		return err
	}
	comparison, err := verificationComparison(ctx, tx, *v)
	if err != nil {
		return err
	}
	v.Report, _ = json.Marshal(map[string]any{"comparison": comparison, "baseline_diagnosis_id": fmt.Sprint(source.ID), "candidate_diagnosis_id": fmt.Sprint(run.ID), "candidate_report": run.Report})
	v.Status = "success"
	return nil
}
