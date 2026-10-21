// 明确的修复协议夹具；验证恢复/收据门禁，不把构造的计数当作真实业务性能结果。
package service

import (
	"encoding/json"
	"strings"
	"testing"

	"drop/api/internal/repository"
)

func genericRepairReceiptFixture() map[string]any {
	content := "--- a/business.py\n+++ b/business.py\n@@ -1 +1 @@\n-before\n+after\n--- /dev/null\n+++ b/drop_regression/test_edge.py\n@@ -0,0 +1 @@\n+assert True\n"
	return map[string]any{
		"output_contract": "drop-python-project-v1", "success": true, "accepted": true, "source_revision": strings.Repeat("a", 64),
		"functional_regression": "passed", "root_cause_status": "validated_in_controlled_python_benchmark", "original_tests_unchanged": true,
		"regression_test_validation": map[string]any{"baseline_failed": true, "candidate_passed": true, "same_test_files": true,
			"baseline": map[string]any{"tests": 1, "failures": 1, "errors": 0, "skipped": 0}, "candidate": map[string]any{"tests": 1, "failures": 0, "errors": 0, "skipped": 0}},
		"negative_control": map[string]any{"stable": true},
		"patch_bundle":     map[string]any{"schema_version": 1, "format": "unified-diff", "content": content, "sha256": repository.Digest(content), "source_revision": strings.Repeat("a", 64), "code_files": []string{"business.py"}, "test_files": []string{"drop_regression/test_edge.py"}, "applied_to": "isolated_snapshot", "production_deployed": false},
	}
}

func TestGenericRepairReceiptRequiresDefectRegressionNegativeControlAndBundle(t *testing.T) {
	for _, failure := range []string{"none", "missing_contract", "no_red", "test_error", "candidate_error", "different_count", "changed_original", "negative_unstable", "bundle_hash", "published"} {
		t.Run(failure, func(t *testing.T) {
			value := genericRepairReceiptFixture()
			regression := value["regression_test_validation"].(map[string]any)
			switch failure {
			case "missing_contract":
				delete(value, "output_contract")
			case "no_red":
				regression["baseline_failed"] = false
			case "test_error":
				regression["baseline"].(map[string]any)["errors"] = 1
			case "candidate_error":
				regression["candidate"].(map[string]any)["errors"] = 1
			case "different_count":
				regression["candidate"].(map[string]any)["tests"] = 2
			case "changed_original":
				value["original_tests_unchanged"] = false
			case "negative_unstable":
				value["negative_control"].(map[string]any)["stable"] = false
			case "bundle_hash":
				value["patch_bundle"].(map[string]any)["sha256"] = strings.Repeat("0", 64)
			case "published":
				value["patch_bundle"].(map[string]any)["production_deployed"] = true
			}
			raw, _ := json.Marshal(value)
			if err := validateGenericRepairReceipt(raw); (err == nil) != (failure == "none") {
				t.Fatalf("unexpected receipt result: %v", err)
			}
		})
	}
	if err := validateGenericRepairReceipt(json.RawMessage(`{"success":true,"accepted":true,"root_cause_status":"validated_in_controlled_saber_benchmark"}`)); err != nil {
		t.Fatal("旧冻结协议被改变", err)
	}
}

func TestRepairRecoveryRestoresFactsAndUniqueSandboxBudget(t *testing.T) {
	revision := strings.Repeat("a", 64)
	profile := json.RawMessage(`{"success":true,"source_revision":"` + revision + `"}`)
	read := json.RawMessage(`{"path":"business.py","sha256":"` + revision + `"}`)
	receipt := genericRepairReceiptFixture()
	receipt["job_key"] = strings.Repeat("b", 64)
	tested, _ := json.Marshal(receipt)
	steps := []DiagnosisStep{{EvidenceID: "S1", Tool: "profile_business_flow", Observation: profile}, {EvidenceID: "S2", Tool: "read_source", Observation: read}, {EvidenceID: "S3", Tool: "test_patch", Observation: tested}, {EvidenceID: "S4", Tool: "test_patch", Observation: tested}}
	state := &repairToolState{run: CodeRepair{ProjectID: "synthetic", SourceRevision: revision}, steps: &steps}
	if err := restoreRepairToolState(state); err != nil {
		t.Fatal(err)
	}
	if !state.profiled || !state.read || !state.tested || state.tests != 1 || string(state.lastTest) != string(tested) {
		t.Fatal("恢复丢失事实或将缓存计为新测试", state.tests)
	}
	steps[1].EvidenceID = "S99"
	if err := restoreRepairToolState(&repairToolState{run: state.run, steps: &steps}); err == nil {
		t.Fatal("不连续证据被恢复")
	}
}

func TestRepairRecoveryRejectsDifferentSourceUnknownToolAndExcessBudget(t *testing.T) {
	for _, failure := range []string{"different_source", "unknown_tool", "test_before_profile", "three_real_tests"} {
		t.Run(failure, func(t *testing.T) {
			revision := strings.Repeat("a", 64)
			steps := []DiagnosisStep{{EvidenceID: "S1", Tool: "profile_business_flow", Observation: json.RawMessage(`{"success":true}`)}, {EvidenceID: "S2", Tool: "read_source", Observation: json.RawMessage(`{"path":"business.py","sha256":"` + revision + `"}`)}}
			switch failure {
			case "different_source":
				steps[0].Observation = json.RawMessage(`{"success":true,"source_revision":"` + strings.Repeat("b", 64) + `"}`)
			case "unknown_tool":
				steps[0].Tool = "run_shell"
			case "test_before_profile":
				steps[0].Tool = "test_patch"
			case "three_real_tests":
				for i := 0; i < 3; i++ {
					raw, _ := json.Marshal(map[string]any{"success": false, "job_key": strings.Repeat(string(rune('c'+i)), 64)})
					steps = append(steps, DiagnosisStep{EvidenceID: []string{"S3", "S4", "S5"}[i], Tool: "test_patch", Observation: raw})
				}
			}
			if err := restoreRepairToolState(&repairToolState{run: CodeRepair{SourceRevision: revision}, steps: &steps}); err == nil {
				t.Fatal("不安全轨迹被恢复")
			}
		})
	}
}
