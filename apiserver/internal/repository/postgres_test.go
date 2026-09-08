package repository

import "testing"

func TestSameTaskRequestCanonicalizesJSON(t *testing.T) {
	a := []byte(`{"name":"x","agent_id":"a1","options":{"sample_rate":99}}`)
	b := []byte(`{ "options": { "sample_rate": 99 }, "agent_id": "a1", "name": "x" }`)
	if !sameTaskRequest(a, b) {
		t.Fatal("semantically equal requests should match")
	}
	if sameTaskRequest(a, []byte(`{"name":"y","agent_id":"a1"}`)) {
		t.Fatal("different requests must not match")
	}
	if sameTaskRequest([]byte(`not json`), a) {
		t.Fatal("invalid JSON must not match")
	}
}

func TestSameTaskRequestIgnoresPerRequestTraceContext(t *testing.T) {
	first := []byte(`{"name":"x","agent_id":"a1","_trace":{"trace_id":"11111111111111111111111111111111"}}`)
	second := []byte(`{"agent_id":"a1","name":"x","_trace":{"trace_id":"22222222222222222222222222222222"}}`)
	if !sameTaskRequest(first, second) {
		t.Fatal("trace context must not turn an idempotent retry into a conflict")
	}
}
