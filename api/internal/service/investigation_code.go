// Package service：在调查与源码修复之间建立经过验证的关联，核对现场目标、已登记项目和不可变源码版本。
// 只有运维 broker 的确切绑定可以触发后续修复，模型猜测的函数或路径不能建立身份关系。
package service

import (
	"context"
	"drop/api/internal/domain"
	"encoding/json"
	"fmt"
	"gorm.io/gorm"
)

// codeRuntimeBinding 由运维控制的 broker 将源码版本绑定到确切的现场进程。
// 模型不能凭相似函数名推断源码与当前运行目标是同一个版本。
type codeRuntimeBinding struct {
	ID       string `json:"id"`
	Revision string `json:"source_revision"`
	Runtime  *struct {
		AgentID  string `json:"agent_id"`
		PID      int    `json:"pid"`
		UID      uint32 `json:"uid"`
		Start    uint64 `json:"start_time_ticks"`
		Revision string `json:"source_revision"`
	} `json:"runtime"`
}

func (b codeRuntimeBinding) validate(agent string, p domain.Parameters) error {
	t, r := p.Target, b.Runtime
	if len(b.Revision) != 64 || r == nil || r.Revision != b.Revision || r.AgentID != agent || p.Scope != "process" || t == nil || t.PID != r.PID || t.StartTimeTicks == 0 || t.StartTimeTicks != r.Start || !t.CheckUID || t.ExpectedUID != r.UID {
		return domain.Fail("TARGET_NOT_ALLOWED", "源码版本未绑定到当前节点及进程身份，请选择已接入的项目目标")
	}
	return nil
}

// advanceLinkedCodeRepair 核验已登记源码与本次现场目标、版本的绑定，再关联或创建修复运行。
// 源码修复成功指隔离快照上的回归和性能对照通过，不能扩展为生产版本已部署。
func (s *Suggestions) advanceLinkedCodeRepair(ctx context.Context, tx *gorm.DB, u domain.User, run *Investigation, verification json.RawMessage) error {
	if !u.IsPlatformAdmin() {
		return domain.Fail("AUTHORIZATION_REVOKED", "源码权限已撤销")
	}
	if run.CodeSourceRevision == nil {
		return domain.Fail("INVALID_STATE", "调查缺少源码版本")
	}
	if run.CodeRepairID == nil {
		goalRunes := []rune(run.Goal)
		if len(goalRunes) > 400 {
			goalRunes = goalRunes[:400]
		}
		// RequestCodeRepair 创建作业之前还会重新核验这个不可变源码版本，不能只相信前面的查询结果。
		job, err := s.RequestCodeRepair(ctx, u, CodeRepairInput{ProjectID: *run.CodeProjectID, SourceRevision: *run.CodeSourceRevision, Goal: string(goalRunes) + "\n针对绑定项目的登记业务函数定位源码热点、生成优化补丁并运行原功能回归和交替CPU对照；明确结论的受控基准范围。", Consent: true}, fmt.Sprintf("investigation:%d:code", run.ID), "investigation-worker")
		if err != nil {
			return err
		}
		run.CodeRepairID = &job.ID
		return nil
	}
	var job CodeRepair
	if err := tx.Where("id=?", *run.CodeRepairID).First(&job).Error; err != nil {
		return err
	}
	if job.SourceRevision != *run.CodeSourceRevision || job.ProjectID != *run.CodeProjectID {
		return domain.Fail("INPUT_CHANGED", "源码修复版本改变")
	}
	if job.Status == "failed" || job.Status == "canceled" {
		return domain.Fail("CODE_REPAIR_FAILED", "源码修复未通过，保留工具证据供审阅")
	}
	if job.Status != "success" {
		return nil
	}
	var report map[string]json.RawMessage
	if json.Unmarshal(verification, &report) != nil {
		return domain.Fail("INVALID_EVIDENCE", "复测结果无效")
	}
	report["code_validation"] = job.Report
	status, err := validatedCodeStatus(job.Report)
	if err != nil {
		return err
	}
	report["root_cause_status"], _ = json.Marshal(status)
	run.Report, _ = json.Marshal(report)
	run.Status = "success"
	return nil
}

func validatedCodeStatus(raw json.RawMessage) (string, error) {
	var report struct {
		Validation struct {
			Accepted   bool   `json:"accepted"`
			Regression string `json:"functional_regression"`
			Status     string `json:"root_cause_status"`
		} `json:"validation"`
	}
	if json.Unmarshal(raw, &report) == nil && report.Validation.Accepted && report.Validation.Regression == "passed" {
		var envelope struct {
			Validation json.RawMessage `json:"validation"`
		}
		if json.Unmarshal(raw, &envelope) != nil || validateGenericRepairReceipt(envelope.Validation) != nil {
			return "", domain.Fail("INVALID_EVIDENCE", "通用源码修复缺少真实缺陷回归或补丁交付")
		}
		switch report.Validation.Status {
		case "validated_in_controlled_crm_benchmark", "validated_in_controlled_saber_benchmark", "validated_in_controlled_python_benchmark":
			return report.Validation.Status, nil
		}
	}
	return "", domain.Fail("INVALID_EVIDENCE", "隔离功能回归或性能验证结论无效")
}
