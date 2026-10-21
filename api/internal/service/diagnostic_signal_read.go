// 历史诊断的业务信号权限独立于profile任务共享。
// 用户可以仍然读取采样任务，但失去某个业务数据源授权后不能继续读取含该信号的历史轨迹或结论。
package service

import (
	"context"
	"drop/api/internal/domain"
	"encoding/json"
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
