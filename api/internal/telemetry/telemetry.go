package telemetry

import (
	"context"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"google.golang.org/grpc"
	"google.golang.org/grpc/status"
	"gorm.io/gorm"
)

type Metrics struct {
	Registry     *prometheus.Registry
	HTTPRequests *prometheus.CounterVec
	HTTPDuration *prometheus.HistogramVec
	RPCRequests  *prometheus.CounterVec
	RPCDuration  *prometheus.HistogramVec
	WebErrors    *prometheus.CounterVec
}

func New(db *gorm.DB, certs map[string]string) *Metrics {
	m := &Metrics{Registry: prometheus.NewRegistry(), HTTPRequests: prometheus.NewCounterVec(prometheus.CounterOpts{Name: "http_requests_total", Help: "Completed API HTTP requests."}, []string{"method", "route", "status"}), HTTPDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{Name: "http_request_duration_seconds", Help: "API HTTP request duration.", Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30}}, []string{"method", "route", "status"}), RPCRequests: prometheus.NewCounterVec(prometheus.CounterOpts{Name: "drop_grpc_client_requests_total", Help: "Completed outgoing gRPC calls."}, []string{"method", "code"}), RPCDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{Name: "drop_grpc_client_duration_seconds", Help: "Outgoing gRPC duration."}, []string{"method"})}
	m.WebErrors = prometheus.NewCounterVec(prometheus.CounterOpts{Name: "drop_web_errors_total", Help: "Authenticated client errors with fixed allowlisted codes."}, []string{"code"})
	m.Registry.MustRegister(m.HTTPRequests, m.HTTPDuration, m.RPCRequests, m.RPCDuration, m.WebErrors, prometheus.NewGoCollector(), prometheus.NewProcessCollector(prometheus.ProcessCollectorOpts{}))
	if db != nil {
		m.Registry.MustRegister(&databaseCollector{DB: db, Certs: certs})
	}
	return m
}
func (m *Metrics) UnaryInterceptor(ctx context.Context, method string, req, reply any, cc *grpc.ClientConn, invoke grpc.UnaryInvoker, opts ...grpc.CallOption) error {
	start := time.Now()
	err := invoke(ctx, method, req, reply, cc, opts...)
	m.RPCRequests.WithLabelValues(method, status.Code(err).String()).Inc()
	m.RPCDuration.WithLabelValues(method).Observe(time.Since(start).Seconds())
	return err
}
func Configure(ctx context.Context, endpoint, service, version string) (func(context.Context) error, error) {
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	if endpoint == "" {
		return func(context.Context) error { return nil }, nil
	}
	u, err := url.Parse(endpoint)
	if err != nil || u.Host == "" {
		return nil, fmt.Errorf("invalid OTLP endpoint")
	}
	if u.Path == "" || u.Path == "/" {
		u.Path = "/v1/traces"
	}
	endpoint = strings.TrimRight(u.String(), "/")
	exp, err := otlptracehttp.New(ctx, otlptracehttp.WithEndpointURL(endpoint))
	if err != nil {
		return nil, err
	}
	provider := sdktrace.NewTracerProvider(sdktrace.WithBatcher(exp, sdktrace.WithMaxQueueSize(512), sdktrace.WithMaxExportBatchSize(128), sdktrace.WithBatchTimeout(2*time.Second)), sdktrace.WithSampler(sdktrace.ParentBased(sdktrace.TraceIDRatioBased(.1))), sdktrace.WithResource(resource.NewSchemaless(attribute.String("service.name", service), attribute.String("service.version", version))))
	otel.SetTracerProvider(provider)
	return provider.Shutdown, nil
}

type databaseCollector struct {
	DB    *gorm.DB
	Certs map[string]string
}

func (c *databaseCollector) Describe(ch chan<- *prometheus.Desc) {}
func (c *databaseCollector) Collect(ch chan<- prometheus.Metric) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	db := c.DB.WithContext(ctx)
	failed := false
	gauge := func(name, help string, v float64, labels []string, values ...string) {
		ch <- prometheus.MustNewConstMetric(prometheus.NewDesc(name, help, labels, nil), prometheus.GaugeValue, v, values...)
	}
	scalar := func(name, query string) {
		var v float64
		if err := db.Raw(query).Scan(&v).Error; err != nil {
			failed = true
			return
		}
		gauge(name, name, v, nil)
	}
	scalar("drop_agents_online", `SELECT count(*) FROM agents WHERE last_seen_at>now()-interval '45 seconds' AND status NOT IN ('disabled','offline')`)
	scalar("drop_task_queue_depth", `SELECT count(*) FROM tasks WHERE collection_status IN ('CREATED','QUEUED') AND target_agent_id IS NOT NULL`)
	scalar("drop_task_queue_oldest_age_seconds", `SELECT coalesce(max(extract(epoch FROM now()-created_at)),0) FROM tasks WHERE collection_status IN ('CREATED','QUEUED') AND target_agent_id IS NOT NULL`)
	scalar("drop_tasks_overdue", `SELECT count(*) FROM tasks WHERE collection_deadline<now() AND collection_status IN ('CREATED','QUEUED','DELIVERED','RUNNING','UPLOADING')`)
	scalar("drop_analysis_oldest_age_seconds", `SELECT coalesce(max(extract(epoch FROM now()-created_at)),0) FROM analysis_jobs WHERE status IN ('pending','retry')`)
	scalar("drop_analysis_expired_leases", `SELECT count(*) FROM analysis_jobs WHERE status='running' AND lease_expires_at<now()`)
	scalar("drop_artifacts_expired", `SELECT count(*) FROM artifacts WHERE status='ready' AND expires_at<now()`)
	scalar("drop_outbox_pending", `SELECT count(*) FROM outbox WHERE published_at IS NULL`)
	scalar("drop_composites_running", `SELECT count(*) FROM composite_runs WHERE status IN ('PENDING','RUNNING')`)
	var agents []struct {
		ID  string
		Age float64
	}
	if err := db.Raw(`SELECT id,extract(epoch FROM now()-last_seen_at) AS age FROM agents ORDER BY id LIMIT 500`).Scan(&agents).Error; err != nil {
		failed = true
	}
	for _, a := range agents {
		gauge("drop_agent_heartbeat_age_seconds", "Seconds since actual Agent heartbeat.", max(0, a.Age), []string{"agent"}, a.ID)
	}
	var jobs []struct {
		Status, Pipeline string
		Count            float64
	}
	if err := db.Raw(`SELECT status,pipeline,count(*) AS count FROM analysis_jobs GROUP BY status,pipeline`).Scan(&jobs).Error; err != nil {
		failed = true
	}
	for _, j := range jobs {
		gauge("drop_analysis_jobs", "Persisted analysis jobs by status and pipeline.", j.Count, []string{"status", "pipeline"}, j.Status, j.Pipeline)
	}
	if sqlDB, err := c.DB.DB(); err == nil {
		stats := sqlDB.Stats()
		ch <- prometheus.MustNewConstMetric(prometheus.NewDesc("drop_db_pool_wait_seconds_total", "Total database pool wait seconds.", nil, nil), prometheus.CounterValue, stats.WaitDuration.Seconds())
		gauge("drop_db_pool_connections", "Open database connections.", float64(stats.OpenConnections), nil)
	}
	for role, path := range c.Certs {
		b, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		block, _ := pem.Decode(b)
		if block == nil {
			continue
		}
		cert, err := x509.ParseCertificate(block.Bytes)
		if err != nil {
			continue
		}
		gauge("drop_certificate_expiry_seconds", "Seconds until mTLS certificate expiry.", time.Until(cert.NotAfter).Seconds(), []string{"role"}, role)
	}
	value := 0.
	if failed {
		value = 1
	}
	gauge("drop_metrics_collection_error", "A database metric query failed.", value, nil)
}
func HTTPLabels(method, route string, status int) (string, string, string) {
	if route == "" {
		route = "unmatched"
	}
	return method, route, strconv.Itoa(status)
}
func TaskAttributes(id int64) []attribute.KeyValue {
	return []attribute.KeyValue{attribute.String("task.id", fmt.Sprint(id))}
}
