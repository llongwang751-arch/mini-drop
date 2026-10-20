package domain

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestValidateTaskRejectsUnsafeAndUnsupportedInput(t *testing.T) {
	cases := []struct {
		name  string
		input CreateTask
		code  string
	}{
		{"unknown kind", CreateTask{Kind: "UNTRUSTED_SCRIPT", AgentID: "node", Parameters: Parameters{PID: 7, DurationSeconds: 5}}, "UNSUPPORTED_KIND"},
		{"negative pid", CreateTask{Kind: "PERF_CPU", AgentID: "node", Parameters: Parameters{PID: -2, DurationSeconds: 5, FrequencyHz: 99}}, "INVALID_PARAMETERS"},
		{"excessive frequency", CreateTask{Kind: "PERF_CPU", AgentID: "node", Parameters: Parameters{PID: 1, DurationSeconds: 5, FrequencyHz: 10001}}, "INVALID_PARAMETERS"},
		{"excessive duration", CreateTask{Kind: "PROC_CPU", AgentID: "node", Parameters: Parameters{PID: 1, DurationSeconds: 301}}, "INVALID_PARAMETERS"},
		{"missing target", CreateTask{Kind: "PROC_CPU", Parameters: Parameters{PID: 1, DurationSeconds: 5}}, "INVALID_PARAMETERS"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.input.Validate()
			if err == nil || err.Code != tc.code {
				t.Fatalf("got %v, want %s", err, tc.code)
			}
		})
	}
}

func TestValidateBoundedRealTask(t *testing.T) {
	for _, kind := range []string{"PERF_CPU", "PROC_CPU"} {
		in := CreateTask{Kind: kind, AgentID: "node", Parameters: Parameters{PID: 7, DurationSeconds: 5, FrequencyHz: 10}}
		if err := in.Validate(); err != nil {
			t.Fatal(err)
		}
	}
}

func TestCollectionPriorityBounds(t *testing.T) {
	for _, priority := range []int{-11, -10, 0, 10, 11} {
		in := CreateTask{Kind: "IMPORT_COLLAPSED", CollapsedText: "imported-fixture;main 1", Priority: priority}
		err := in.Validate()
		if (priority < -10 || priority > 10) != (err != nil) {
			t.Fatalf("priority %d: got error %v", priority, err)
		}
	}
}

func TestResourceOwnershipAndRole(t *testing.T) {
	if (User{ID: "alice", Role: "operator"}).CanRead("bob") {
		t.Fatal("operator must not read another owner's task")
	}
	if !(User{ID: "root", Role: "admin"}).CanRead("bob") {
		t.Fatal("admin must read tasks")
	}
	if (User{ID: "alice", Role: "viewer"}).CanWrite() {
		t.Fatal("viewer must not write")
	}
}

func TestCredentialAndObjectKeysNeverSerializeToBrowser(t *testing.T) {
	values := []any{User{ID: "u", PasswordHash: "private-password-hash"}, Attempt{ID: 1, RawObjectKey: "private-object-key", RawUploadURL: "private-upload-signature"}, Artifact{ID: 1, ObjectKey: "private-object-key"}}
	for _, v := range values {
		body, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(body), "private-") {
			t.Fatal("private storage or authentication fields leaked to JSON")
		}
	}
}
