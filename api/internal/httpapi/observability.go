package httpapi

import (
	"drop/api/internal/telemetry"
	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/propagation"
	"time"
)

func (s *Server) observe() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		ctx := otel.GetTextMapPropagator().Extract(c.Request.Context(), propagation.HeaderCarrier(c.Request.Header))
		ctx, span := otel.Tracer("drop/api/http").Start(ctx, c.Request.Method+" HTTP")
		defer span.End()
		c.Request = c.Request.WithContext(ctx)
		c.Next()
		method, route, status := telemetry.HTTPLabels(c.Request.Method, c.FullPath(), c.Writer.Status())
		s.Telemetry.HTTPRequests.WithLabelValues(method, route, status).Inc()
		s.Telemetry.HTTPDuration.WithLabelValues(method, route, status).Observe(time.Since(start).Seconds())
		span.SetName(method + " " + route)
		span.SetAttributes(attribute.String("http.request.method", method), attribute.String("http.route", route), attribute.Int("http.response.status_code", c.Writer.Status()), attribute.String("request.id", c.GetString("request_id")))
	}
}
func (s *Server) prometheus(c *gin.Context) {
	promhttp.HandlerFor(s.Telemetry.Registry, promhttp.HandlerOpts{Timeout: 5 * time.Second}).ServeHTTP(c.Writer, c.Request)
}
