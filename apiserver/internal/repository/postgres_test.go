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
