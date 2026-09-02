package objectstore

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestPresignPutScopesAuthorizationToExactObject(t *testing.T) {
	store, err := New("minio:9000", "access", "secret", false)
	if err != nil {
		t.Fatal(err)
	}
	url, err := store.PresignPut(
		context.Background(), "mini-drop", "tasks/task-1/perf.data", 30*time.Minute,
	)
	if err != nil {
		t.Fatal(err)
	}
	if url.Scheme != "http" || url.Host != "minio:9000" {
		t.Fatalf("unexpected signed endpoint: %s", url.Redacted())
	}
	if url.Path != "/mini-drop/tasks/task-1/perf.data" {
		t.Fatalf("authorization escaped exact object: %s", url.Path)
	}
	if !strings.Contains(url.RawQuery, "X-Amz-Signature=") {
		t.Fatal("presigned PUT URL has no signature")
	}
}

func TestPresignPutUsesAgentReachableEndpoint(t *testing.T) {
	store, err := NewWithSignerEndpoint(
		"minio:9000", "storage.example.test:9443",
		"access", "secret", false, true,
	)
	if err != nil {
		t.Fatal(err)
	}
	signed, err := store.PresignPut(
		context.Background(), "mini-drop",
		"tasks/task-1/attempts/attempt-1/raw/perf.data", 5*time.Minute,
	)
	if err != nil {
		t.Fatal(err)
	}
	if signed.Scheme != "https" || signed.Host != "storage.example.test:9443" {
		t.Fatalf("signed URL is not Agent-reachable: %s", signed.Redacted())
	}
}
