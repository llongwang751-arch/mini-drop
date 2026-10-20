// 本文件验证现场进程、源码项目与版本绑定，防止 PID 复用、目标切换或相似名称错误触发关联修复。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package service

import (
	"drop/api/internal/domain"
	"encoding/json"
	"strings"
	"testing"
)

func TestDiagnosisSourceRuntimeBindingRejectsReusedOrDifferentTarget(t *testing.T) {
	rev := strings.Repeat("a", 64)
	raw := `{"id":"crm-python","source_revision":"` + rev + `","runtime":{"agent_id":"node","pid":41,"uid":10001,"start_time_ticks":123,"source_revision":"` + rev + `"}}`
	var binding codeRuntimeBinding
	if err := json.Unmarshal([]byte(raw), &binding); err != nil {
		t.Fatal(err)
	}
	target := domain.Target{Type: "process", PID: 41, StartTimeTicks: 123, CheckUID: true, ExpectedUID: 10001}
	params := domain.Parameters{Scope: "process", PID: 41, Target: &target}
	if err := binding.validate("node", params); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*domain.Target){func(t *domain.Target) { t.PID++ }, func(t *domain.Target) { t.StartTimeTicks++ }, func(t *domain.Target) { t.ExpectedUID++ }, func(t *domain.Target) { t.CheckUID = false }} {
		changed := target
		change(&changed)
		p := params
		p.Target = &changed
		if binding.validate("node", p) == nil {
			t.Fatal("accepted mismatched process identity")
		}
	}
	if binding.validate("other-node", params) == nil {
		t.Fatal("accepted another Agent")
	}
	binding.Runtime.Revision = strings.Repeat("b", 64)
	if binding.validate("node", params) == nil {
		t.Fatal("accepted another source version")
	}
}

func TestDiagnosisLinkedSourceValidationRetainsProjectScope(t *testing.T) {
	for _, status := range []string{"validated_in_controlled_crm_benchmark", "validated_in_controlled_saber_benchmark"} {
		raw := json.RawMessage(`{"validation":{"accepted":true,"functional_regression":"passed","root_cause_status":"` + status + `"}}`)
		got, err := validatedCodeStatus(raw)
		if err != nil || got != status {
			t.Fatal("lost independently validated project scope", got, err)
		}
	}
	for _, raw := range []string{
		`{"validation":{"accepted":true}}`,
		`{"validation":{"accepted":false,"functional_regression":"passed","root_cause_status":"validated_in_controlled_saber_benchmark"}}`,
		`{"validation":{"accepted":true,"functional_regression":"failed","root_cause_status":"validated_in_controlled_crm_benchmark"}}`,
		`{"validation":{"accepted":true,"functional_regression":"passed","root_cause_status":"confirmed_production_root_cause"}}`,
	} {
		if _, err := validatedCodeStatus(json.RawMessage(raw)); err == nil {
			t.Fatal("accepted missing, failed or unbounded claim", raw)
		}
	}
}
