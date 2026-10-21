// 修复恢复只重建已持久化的工具事实；模型摘要、猜测的测试结果或其他修复的收据不能恢复资格。
package service

import (
	"encoding/json"
	"fmt"
	"strings"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
)

// restoreRepairToolState 保持 S 编号连续、两次真实沙箱测试预算及最后一次有效验收。
// SDK 的 completed receipt 回放不会再次 Invoke，因此恢复必须在进入 SDK 前完成。
func restoreRepairToolState(state *repairToolState) error {
	if len(*state.steps) > 12 {
		return domain.Fail("AGENT_CHECKPOINT_INVALID", "修复轨迹超过原有工具预算")
	}
	for i, step := range *state.steps {
		if step.EvidenceID != fmt.Sprintf("S%d", i+1) || len(step.Observation) > 49152 {
			return domain.Fail("AGENT_CHECKPOINT_INVALID", "修复证据编号或大小不可恢复")
		}
		var observed struct {
			Success  *bool  `json:"success"`
			Accepted bool   `json:"accepted"`
			Revision string `json:"source_revision"`
			Project  string `json:"project_id"`
			JobKey   string `json:"job_key"`
			Path     string `json:"path"`
			SHA      string `json:"sha256"`
		}
		if json.Unmarshal(step.Observation, &observed) != nil || observed.Revision != "" && observed.Revision != state.run.SourceRevision || observed.Project != "" && observed.Project != state.run.ProjectID {
			return domain.Fail("AGENT_CHECKPOINT_INVALID", "修复工具收据不属于当前源码版本")
		}
		switch step.Tool {
		case "profile_business_flow":
			state.profiled = observed.Success != nil && *observed.Success
		case "read_source":
			// 只有实际源码结果能恢复读取资格；错误观测不会被误认成一次有效读取。
			if observed.Path != "" && len(observed.SHA) == 64 {
				state.read = true
			}
		case "test_patch":
			if observed.JobKey != "" || observed.Success != nil && *observed.Success {
				if !state.profiled || !state.read {
					return domain.Fail("AGENT_CHECKPOINT_INVALID", "测试收据先于真实profile和源码读取")
				}
				if err := state.observeTestReceipt(step.Observation); err != nil {
					return err
				}
			}
		default:
			return domain.Fail("AGENT_CHECKPOINT_INVALID", "恢复轨迹含未知修复工具")
		}
	}
	if state.tests > 2 {
		return domain.Fail("AGENT_CHECKPOINT_INVALID", "修复沙箱测试预算已经耗尽")
	}
	return nil
}

// observeTestReceipt 同一 repair 的同一 job 缓存仍保留可审计观测，但不伪装成第二次真实测量。
// 无 job_key 的旧协议夹具按一次调用计数；生产 broker 的每份实际收据都带有作业 SHA。
func (state *repairToolState) observeTestReceipt(raw json.RawMessage) error {
	var value struct {
		Success  bool   `json:"success"`
		Accepted bool   `json:"accepted"`
		JobKey   string `json:"job_key"`
	}
	if json.Unmarshal(raw, &value) != nil {
		return domain.Fail("INVALID_EVIDENCE", "测试收据无效")
	}
	if state.testJobs == nil {
		state.testJobs = map[string]bool{}
	}
	if value.JobKey == "" || !state.testJobs[value.JobKey] {
		state.tests++
	}
	if value.JobKey != "" {
		state.testJobs[value.JobKey] = true
	}
	state.lastTest = raw
	state.tested = value.Success && value.Accepted
	if state.tested {
		return validateGenericRepairReceipt(raw)
	}
	return nil
}

// validateGenericRepairReceipt 额外收紧新项目的成功资格，旧冻结 preset 的结果继续走原协议。
// 不能仅看 accepted=true：新增测试须修复前真实断言失败、修复后通过，且 sham 负控稳定。
func validateGenericRepairReceipt(raw json.RawMessage) error {
	var value struct {
		Contract string `json:"output_contract"`
		Status   string `json:"root_cause_status"`
		Success  bool   `json:"success"`
		Accepted bool   `json:"accepted"`
		Generic  struct {
			BaselineFailed  bool                                           `json:"baseline_failed"`
			CandidatePassed bool                                           `json:"candidate_passed"`
			SameTests       bool                                           `json:"same_test_files"`
			Baseline        struct{ Tests, Failures, Errors, Skipped int } `json:"baseline"`
			Candidate       struct{ Tests, Failures, Errors, Skipped int } `json:"candidate"`
		} `json:"regression_test_validation"`
		Original bool `json:"original_tests_unchanged"`
		Negative struct {
			Stable bool `json:"stable"`
		} `json:"negative_control"`
		Bundle struct {
			Version  int      `json:"schema_version"`
			Format   string   `json:"format"`
			Content  string   `json:"content"`
			SHA      string   `json:"sha256"`
			Revision string   `json:"source_revision"`
			Files    []string `json:"code_files"`
			Tests    []string `json:"test_files"`
			Applied  string   `json:"applied_to"`
			Deployed bool     `json:"production_deployed"`
		} `json:"patch_bundle"`
		Revision string `json:"source_revision"`
	}
	if json.Unmarshal(raw, &value) != nil {
		return domain.Fail("INVALID_EVIDENCE", "修复收据无效")
	}
	if value.Contract == "" && value.Status != "validated_in_controlled_python_benchmark" {
		return nil
	}
	bad := func() error {
		return domain.Fail("INVALID_EVIDENCE", "通用项目缺陷回归、负控或补丁交付验收不成立")
	}
	if value.Contract != "drop-python-project-v1" || !value.Success || !value.Accepted || !value.Original || !value.Negative.Stable || !value.Generic.BaselineFailed || !value.Generic.CandidatePassed || !value.Generic.SameTests || value.Generic.Baseline.Tests < 1 || value.Generic.Baseline.Failures < 1 || value.Generic.Baseline.Errors != 0 || value.Generic.Baseline.Skipped != 0 || value.Generic.Candidate.Tests != value.Generic.Baseline.Tests || value.Generic.Candidate.Failures != 0 || value.Generic.Candidate.Errors != 0 || value.Generic.Candidate.Skipped != 0 {
		return bad()
	}
	if value.Bundle.Version != 1 || value.Bundle.Format != "unified-diff" || value.Bundle.Applied != "isolated_snapshot" || value.Bundle.Deployed || value.Bundle.Revision != value.Revision || len(value.Revision) != 64 || len(value.Bundle.Content) == 0 || len(value.Bundle.Content) > 24576 || repository.Digest(value.Bundle.Content) != value.Bundle.SHA || len(value.Bundle.Files) == 0 || len(value.Bundle.Tests) == 0 || !strings.Contains(value.Bundle.Content, "--- /dev/null\n") {
		return bad()
	}
	return nil
}
