// 在成功终态提交前重新核验已实际读取的数据源授权。
// 别名位置由登记快照决定；组成员/目标授权被撤销后，不允许迟到模型结果继续附带旧业务信号。
package service

import (
	"context"
	"encoding/json"
	"slices"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

func (s *Suggestions) pinDiagnosticSignals(ctx context.Context, tx *gorm.DB, run DiagnosisRun, trace []DiagnosisStep) error {
	if run.TelemetryServiceID == "" {
		return nil
	}
	if s.Observability == nil {
		return domain.Fail("INPUT_CHANGED", "业务数据源登记不可用")
	}
	refs := []string{}
	for _, step := range trace {
		switch step.Tool {
		case "read_profile_summary":
			var value struct {
				Revision string `json:"observability_revision"`
			}
			if json.Unmarshal(step.Observation, &value) != nil || value.Revision != s.Observability.Digest {
				return domain.Fail("INPUT_CHANGED", "业务数据源登记版本已改变")
			}
		case "query_business_signals":
			var value struct {
				Available  bool   `json:"available"`
				ServiceRef string `json:"service_ref"`
			}
			if json.Unmarshal(step.Observation, &value) != nil {
				return domain.Fail("INVALID_EVIDENCE", "业务信号观测格式无效")
			}
			if value.Available {
				refs = append(refs, value.ServiceRef)
			}
		case "inspect_service_topology":
			var value struct {
				Nodes []struct {
					ServiceRef string `json:"service_ref"`
				} `json:"nodes"`
			}
			if json.Unmarshal(step.Observation, &value) != nil {
				return domain.Fail("INVALID_EVIDENCE", "拓扑观测格式无效")
			}
			for _, node := range value.Nodes {
				refs = append(refs, node.ServiceRef)
			}
		}
	}
	var task domain.Task
	if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=?", run.TaskID).First(&task).Error; err != nil {
		return err
	}
	var u domain.User
	if err := tx.Clauses(clause.Locking{Strength: "SHARE"}).Where("id=?", run.RequestedBy).First(&u).Error; err != nil {
		return err
	}
	local := *s
	local.Repo = repository.New(tx)
	if err := local.validateTelemetryService(ctx, u, run.TelemetryServiceID, diagnosticAgentID(task)); err != nil {
		return domain.Fail("AUTHORIZATION_REVOKED", "业务信号权限已撤销")
	}
	services, err := local.telemetryScope(ctx, run)
	if err != nil {
		return err
	}
	groupIDs := []string{}
	agentIDs := []string{}
	refs = append(refs, "S1")
	slices.Sort(refs)
	refs = slices.Compact(refs)
	for _, ref := range refs {
		if len(ref) != 2 || ref[0] != 'S' || ref[1] < '1' || ref[1] > '8' {
			return domain.Fail("INVALID_EVIDENCE", "业务信号别名无效")
		}
		index := int(ref[1] - '1')
		if index >= len(services) || services[index].ID == "" {
			return domain.Fail("AUTHORIZATION_REVOKED", "已读取业务信号的权限被撤销")
		}
		service := services[index]
		agentIDs = append(agentIDs, service.AgentID)
		if service.GroupID != nil {
			groupIDs = append(groupIDs, *service.GroupID)
		}
	}
	if !u.IsPlatformAdmin() {
		var grants []struct {
			GroupID string
			AgentID string
		}
		if err = tx.Raw(`SELECT group_id,agent_id FROM group_agents WHERE agent_id IN ?
 AND group_id IN (SELECT group_id FROM group_members WHERE user_id=?) ORDER BY group_id,agent_id FOR SHARE`, agentIDs, u.ID).Scan(&grants).Error; err != nil {
			return err
		}
		for _, grant := range grants {
			groupIDs = append(groupIDs, grant.GroupID)
		}
		if len(groupIDs) > 0 {
			var members []struct{ GroupID string }
			if err = tx.Raw("SELECT group_id FROM group_members WHERE user_id=? AND group_id IN ? ORDER BY group_id FOR SHARE", u.ID, groupIDs).Scan(&members).Error; err != nil {
				return err
			}
		}
		// 等待成员/授权行锁期间可能已经降权；固定实际行后用新语句快照再授权。
		for _, ref := range refs {
			service := services[int(ref[1]-'1')]
			if err = local.validateTelemetryService(ctx, u, service.ID, service.AgentID); err != nil {
				return domain.Fail("AUTHORIZATION_REVOKED", "业务信号权限已撤销")
			}
		}
	}
	return nil
}
