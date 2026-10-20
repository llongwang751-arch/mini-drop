package telemetry

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
)

func TestRealOTLPExportAndPropagation(t *testing.T) {
	var requests atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/traces" {
			t.Errorf("unexpected OTLP path %s", r.URL.Path)
		}
		if r.ContentLength == 0 {
			t.Error("empty OTLP payload")
		}
		requests.Add(1)
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.WriteHeader(200)
	}))
	defer srv.Close()
	shutdown, err := Configure(context.Background(), srv.URL, "drop-api-test", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	carrier := propagation.MapCarrier{"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}
	ctx := otel.GetTextMapPropagator().Extract(context.Background(), carrier)
	ctx, span := otel.Tracer("fixture").Start(ctx, "task.fixture")
	out := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, out)
	if out.Get("traceparent") == "" {
		t.Fatal("traceparent lost")
	}
	span.End()
	if err := shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	if requests.Load() == 0 {
		t.Fatal("no actual OTLP HTTP export")
	}
}
