package httpapi

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"drop/api/internal/logging"
	"github.com/gin-gonic/gin"
)

func TestRequestLogUsesTemplateAndTaskContextWithoutPrivateHeaders(t *testing.T) {
	var output bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(logging.New(&output, "drop-api", "fixture"))
	t.Cleanup(func() { slog.SetDefault(previous) })
	// Explicit HTTP-only fixture: no repository or task lifecycle is simulated.
	server := &Server{}
	router := gin.New()
	router.Use(server.requestID())
	router.GET("/api/v1/tasks/:id", func(c *gin.Context) { c.Status(http.StatusOK) })
	request := httptest.NewRequest("GET", "/api/v1/tasks/9007199254740993?signature=IMPORTED_PRIVATE_QUERY", nil)
	request.Header.Set("X-Request-ID", "imported-log-request")
	request.Header.Set("Cookie", "session=IMPORTED_PRIVATE_COOKIE")
	request.Header.Set("Authorization", "Bearer IMPORTED_PRIVATE_TOKEN")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)
	var record map[string]any
	if response.Code != http.StatusOK || json.Unmarshal(output.Bytes(), &record) != nil {
		t.Fatal("HTTP fixture or structured request log failed")
	}
	if record["task_id"] != "9007199254740993" || record["route"] != "/api/v1/tasks/:id" || record["request_id"] != "imported-log-request" || record["stage"] != "http.request" || strings.Contains(output.String(), "IMPORTED_PRIVATE") {
		t.Fatal("Request logging lost known context or leaked private headers/query")
	}
}
