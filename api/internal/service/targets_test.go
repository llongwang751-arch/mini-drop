package service

import (
	"drop/api/internal/domain"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func targetPolicyFixture(t *testing.T) []byte {
	t.Helper()
	kinds := os.Getenv("TEST_TASK_KINDS_FILE")
	if kinds == "" {
		kinds = "../../../contracts/task-kinds.json"
	}
	b, err := os.ReadFile(filepath.Join(filepath.Dir(kinds), "fixtures", "target-policy.json"))
	if err != nil {
		t.Fatal(err)
	}
	return b
}
func TestCanonicalAgentTargetPolicyAndUnlistedProcess(t *testing.T) {
	raw, _ := json.Marshal(map[string]any{"processes": []map[string]any{{"pid": 7, "uid": 10001, "start_time_ticks": 123}}, "target_policy": json.RawMessage(targetPolicyFixture(t))})
	host := domain.CreateTask{Parameters: domain.Parameters{Scope: "host", Target: &domain.Target{Type: "host"}}}
	if err := validateTargetResources(&host, raw); err != nil {
		t.Fatal(err)
	}
	container := domain.CreateTask{Parameters: domain.Parameters{Scope: "container", Target: &domain.Target{Type: "container", ContainerID: strings.Repeat("a", 64)}}}
	if err := validateTargetResources(&container, raw); err != nil {
		t.Fatal(err)
	}
	container.Parameters.Target.ContainerID = "abcdefabcdee"
	if validateTargetResources(&container, raw) == nil {
		t.Fatal("container outside exact allowlist accepted")
	}
	process := domain.CreateTask{Parameters: domain.Parameters{Scope: "process", PID: 99999, Target: &domain.Target{Type: "process", PID: 99999}}}
	if err := validateTargetResources(&process, raw); err != nil {
		t.Fatal("200-process UI list truncation rejected policy-authorized UID", err)
	}
	if !process.Parameters.Target.CheckUID || process.Parameters.Target.ExpectedUID != 10001 {
		t.Fatal("UID not pinned")
	}
	process.Parameters.Target.ExpectedUID = 0
	if validateTargetResources(&process, raw) == nil {
		t.Fatal("unlisted PID bypassed UID policy")
	}
	known := domain.CreateTask{Parameters: domain.Parameters{Scope: "process", PID: 7, Target: &domain.Target{Type: "process", PID: 7}}}
	if err := validateTargetResources(&known, raw); err != nil {
		t.Fatal(err)
	}
	if known.Parameters.Target.StartTimeTicks != 123 || !known.Parameters.Target.CheckUID {
		t.Fatal("known identity not pinned")
	}
	known.Parameters.Target.StartTimeTicks = 124
	if validateTargetResources(&known, raw) == nil {
		t.Fatal("stale process identity accepted")
	}
	if validateTargetResources(&host, []byte(`{"target_policy":{"host_resources":false,"allow_host_resources":true}}`)) == nil {
		t.Fatal("legacy alias overrode canonical deny")
	}
}
