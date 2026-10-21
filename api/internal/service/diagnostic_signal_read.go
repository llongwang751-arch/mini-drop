// 历史诊断的业务信号权限独立于profile任务共享。
// 用户可以仍然读取采样任务，但失去某个业务数据源授权后不能继续读取含该信号的历史轨迹或结论。
package service

import (
	"context"
	"encoding/json"

	"drop/api/internal/domain"
)

func diagnosticSignalRefs(trace []DiagnosisStep) ([]string, error) {
	refs := []string{"S1"}
	for _, step := range trace {
		if step.Tool == "query_business_signals" {
			var observation struct {
				Available  bool   `json:"available"`
				ServiceRef string `json:"service_ref"`
			}
			if json.Unmarshal(step.Observation, &observation) != nil {
				return nil, domain.Fail("INVALID_EVIDENCE", "业务信号观测格式无效")
			}
			if observation.Available {
				refs = append(refs, observation.ServiceRef)
			}
		}
		if step.Tool == "inspect_service_topology" {
			var observation struct {
				Nodes []struct {
					ServiceRef string `json:"service_ref"`
				} `json:"nodes"`
			}
			if json.Unmarshal(step.Observation, &observation) != nil {
				return nil, domain.Fail("INVALID_EVIDENCE", "业务拓扑观测格式无效")
			}
			for _, node := range observation.Nodes {
				refs = append(refs, node.ServiceRef)
			}
		}
	}
	return refs, nil
}

func (s *Suggestions) AuthorizeDiagnosisSignals(ctx context.Context, u domain.User, run DiagnosisRun) error {
	if run.TelemetryServiceID == "" {
		return nil
	}
	current, err := s.Repo.User(ctx, u.ID)
	if err != nil {
		return err
	}
	if s.Observability == nil {
		return domain.Fail("FORBIDDEN", "业务信号登记不可用，不能读取关联诊断")
	}
	var trace []DiagnosisStep
	if json.Unmarshal(run.Trace, &trace) != nil {
		return domain.Fail("INVALID_EVIDENCE", "诊断轨迹无法读取")
	}
	refs, err := diagnosticSignalRefs(trace)
	if err != nil {
		return err
	}
	services := s.Observability.registeredScope(run.TelemetryServiceID)
	for _, ref := range refs {
		if len(ref) != 2 || ref[0] != 'S' || ref[1] < '1' || ref[1] > '8' {
			return domain.Fail("INVALID_EVIDENCE", "业务信号别名无效")
		}
		index := int(ref[1] - '1')
		if index >= len(services) {
			return domain.Fail("FORBIDDEN", "原业务信号作用域已改变")
		}
		service := services[index]
		if err = s.authorizeTelemetryService(ctx, current, service.ID, service.AgentID, false); err != nil {
			return domain.Fail("FORBIDDEN", "无权读取关联业务信号")
		}
	}
	for _, step := range trace {
		if step.Tool == "read_profile_summary" {
			var metadata struct {
				Revision string `json:"observability_revision"`
			}
			if json.Unmarshal(step.Observation, &metadata) != nil || metadata.Revision != s.Observability.Digest {
				return domain.Fail("FORBIDDEN", "原业务信号登记版本已改变")
			}
		}
	}
	return nil
}

// AuthorizeVerificationSignals 覆盖复测报告中的基线/候选诊断副本。
// 任务共享只授予profile操作权限，不能顺带授予被复制业务信号的读取权限；每个实际来源都走原诊断的当前授权检查。
// 调用方仍须保留基线和候选任务的原有权限检查。没有业务信号的旧来源可读；缺少来源或登记配置则失败关闭。
func (s *Suggestions) AuthorizeVerificationSignals(ctx context.Context, u domain.User, verification VerificationRun) error {
	if s == nil || s.Repo == nil {
		return domain.Fail("FORBIDDEN", "无法核验复测诊断来源")
	}
	sources := []struct{ diagnosisID, taskID int64 }{{verification.DiagnosisID, verification.BaselineTaskID}}
	if verification.CandidateDiagnosisID != nil {
		sources = append(sources, struct{ diagnosisID, taskID int64 }{*verification.CandidateDiagnosisID, verification.CandidateTaskID})
	}
	for _, source := range sources {
		if source.diagnosisID <= 0 || source.taskID <= 0 {
			return domain.Fail("INVALID_EVIDENCE", "复测诊断来源无效")
		}
		var run DiagnosisRun
		if err := s.Repo.Context(ctx).Where("id=? AND task_id=?", source.diagnosisID, source.taskID).First(&run).Error; err != nil {
			return domain.Fail("FORBIDDEN", "复测诊断来源无法核实")
		}
		if err := s.AuthorizeDiagnosisSignals(ctx, u, run); err != nil {
			return err
		}
	}
	return nil
}

func (s *Suggestions) authorizeInvestigationSignals(ctx context.Context, u domain.User, run Investigation) error {
	var rows []DiagnosisRun
	if err := s.Repo.Context(ctx).Raw(`SELECT * FROM diagnosis_runs WHERE id=? OR id IN
 (SELECT candidate_diagnosis_id FROM verification_runs WHERE id=?)`, run.DiagnosisID, run.VerificationID).Scan(&rows).Error; err != nil {
		return err
	}
	for _, diagnosis := range rows {
		if err := s.AuthorizeDiagnosisSignals(ctx, u, diagnosis); err != nil {
			return err
		}
	}
	return nil
}
