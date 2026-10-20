package service

import (
	"strings"
	"testing"
)

func TestAIRedactionAndPlainText(t *testing.T) {
	input := []byte(`{"kind":"PERF_CPU","total_samples":100,"node_count":4,"creator_id":"secret-user","flamegraph":{"name":"/secret/raw/path"},"top_functions":[{"name":"/home/customer/app.secret","self_percent":90,"total_percent":95}]}`)
	b, err := RedactedSummary(input)
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range []string{"customer", "secret-user", "raw/path", "app.secret"} {
		if strings.Contains(string(b), s) {
			t.Fatal("identifier leaked")
		}
	}
	if !strings.Contains(string(b), "symbol_") {
		t.Fatal(string(b))
	}
	if got := plainText(`<script>alert(1)</script><b>Evidence</b><img src=x onerror=alert(1)>`); got != "Evidence" {
		t.Fatal(got)
	}
}
