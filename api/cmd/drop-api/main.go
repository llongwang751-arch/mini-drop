// Package main：组合根：读取经过校验的配置，初始化 PostgreSQL、mTLS Core 客户端、对象存储和 HTTP 路由，再启动任务与智能诊断后台循环。
// 各后台循环共享进程取消信号；数据库迁移是独立启动模式，正常服务不能在 schema 缺失时继续运行。
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"

	"drop/api/internal/config"
	"drop/api/internal/control"
	"drop/api/internal/domain"
	"drop/api/internal/httpapi"
	"drop/api/internal/logging"
	"drop/api/internal/repository"
	"drop/api/internal/service"
	"drop/api/internal/storage"
	"drop/api/internal/telemetry"
)

var settings *config.Config

func secret(name, def string) (string, error) {
	if settings == nil {
		return "", config.ErrInvalid
	}
	return settings.Value(name, def)
}
func required(name string) (string, error) {
	v, err := secret(name, "")
	if err != nil {
		return "", err
	}
	if v == "" {
		return "", fmt.Errorf("missing %s", name)
	}
	return v, nil
}

// run 加载配置并构造跨服务依赖，随后启动受同一取消信号控制的后台循环。
// 迁移模式执行完即退出；正常模式要求 schema、mTLS 和必要凭据全部就绪，防止部分初始化的 API 对外接单。
func run() error {
	slog.SetDefault(logging.New(os.Stdout, "drop-api", "0.2.0"))
	var err error
	settings, err = config.Load(os.Args[1:], nil)
	if err != nil {
		return config.ErrInvalid
	}
	slog.Info("effective configuration", "stage", "startup", "effective_config", settings.Effective(), "config_hash", settings.Hash())
	dsn, err := required("DATABASE_URL")
	if err != nil {
		return err
	}
	if parsed, err := url.Parse(dsn); err == nil && (parsed.Scheme == "postgres" || parsed.Scheme == "postgresql") {
		q := parsed.Query()
		q.Set("statement_timeout", "15000")
		parsed.RawQuery = q.Encode()
		dsn = parsed.String()
	}
	db, err := gorm.Open(postgres.New(postgres.Config{DSN: dsn, PreferSimpleProtocol: true}), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent), TranslateError: true})
	if err != nil {
		return errors.New("database initialization failed")
	}
	sqlDB, err := db.DB()
	if err != nil {
		return err
	}
	defer sqlDB.Close()
	sqlDB.SetMaxOpenConns(12)
	sqlDB.SetMaxIdleConns(3)
	sqlDB.SetConnMaxLifetime(30 * time.Minute)
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if settings.Migrate {
		dir, _ := secret("MIGRATIONS_DIR", "/app/db/migrations")
		files, err := filepath.Glob(filepath.Join(dir, "*.sql"))
		if err != nil {
			return err
		}
		sort.Strings(files)
		for _, file := range files {
			b, err := os.ReadFile(file)
			if err != nil {
				return err
			}
			if _, err := sqlDB.ExecContext(ctx, string(b)); err != nil {
				return fmt.Errorf("database migration failed: %s", filepath.Base(file))
			}
		}
		var applied int
		if err := sqlDB.QueryRowContext(ctx, "SELECT max(version) FROM schema_migrations").Scan(&applied); err != nil {
			return errors.New("schema version query failed")
		}
		slog.Info("migration complete", "stage", "migration", "schema_version", applied)
		return nil
	}
	repo := repository.New(db)
	if err := repo.CheckSchema(ctx); err != nil {
		return fmt.Errorf("schema version %d is required; run migrate first", repository.MinimumSchemaVersion)
	}
	values := map[string]string{}
	for _, name := range []string{"TLS_CA_FILE", "TLS_CERT_FILE", "TLS_KEY_FILE", "S3_ACCESS_KEY", "S3_SECRET_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD"} {
		v, err := required(name)
		if err != nil {
			return err
		}
		values[name] = v
	}
	address, _ := secret("DROP_SERVER_ADDRESS", "drop-server:50051")
	otelEndpoint, _ := secret("OTEL_EXPORTER_OTLP_ENDPOINT", "")
	shutdown, err := telemetry.Configure(ctx, otelEndpoint, "drop-api", "0.2.0")
	if err != nil {
		return errors.New("tracing initialization failed")
	}
	defer func() {
		stop, done := context.WithTimeout(context.Background(), 5*time.Second)
		defer done()
		_ = shutdown(stop)
	}()
	metrics := telemetry.New(db, map[string]string{"api": values["TLS_CERT_FILE"]})
	ctrl, err := control.New(address, values["TLS_CA_FILE"], values["TLS_CERT_FILE"], values["TLS_KEY_FILE"], metrics)
	if err != nil {
		return errors.New("mTLS control client initialization failed")
	}
	defer ctrl.Close()
	endpoint, _ := secret("S3_ENDPOINT", "minio:9000")
	bucket, _ := secret("S3_BUCKET", "drop-artifacts")
	public, _ := secret("PUBLIC_URL", "http://localhost")
	store, err := storage.New(endpoint, values["S3_ACCESS_KEY"], values["S3_SECRET_KEY"], bucket, public)
	if err != nil {
		return errors.New("object storage initialization failed")
	}
	accounts := service.NewAccounts(repo)
	if err := accounts.Bootstrap(ctx, values["ADMIN_USERNAME"], values["ADMIN_PASSWORD"]); err != nil {
		return err
	}
	kindsPath, _ := secret("TASK_KINDS_FILE", "/app/contracts/task-kinds.json")
	kindsJSON, err := os.ReadFile(kindsPath)
	if err != nil {
		return err
	}
	var kinds []map[string]any
	if err := json.Unmarshal(kindsJSON, &kinds); err != nil {
		return err
	}
	if err := domain.LoadKinds(kindsJSON); err != nil {
		return err
	}
	tasks := &service.Tasks{Repo: repo, Storage: store, Control: ctrl}
	go tasks.Run(ctx)
	api := &httpapi.Server{Tasks: tasks, Accounts: accounts, Repo: repo, Kinds: kinds, PublicURL: public, Telemetry: metrics, SecureCookie: strings.HasPrefix(public, "https://")}
	issuer, _ := secret("OIDC_ISSUER", "")
	if issuer != "" {
		id, _ := required("OIDC_CLIENT_ID")
		clientSecret, _ := secret("OIDC_CLIENT_SECRET", "")
		redirect, _ := secret("OIDC_REDIRECT_URL", public+"/api/v1/auth/oidc/callback")
		name, _ := secret("OIDC_PROVIDER_NAME", "企业账号")
		allowHTTP, _ := secret("OIDC_ALLOW_HTTP", "false")
		auth, err := service.NewOIDC(ctx, repo, service.OIDCConfig{Issuer: issuer, ClientID: id, ClientSecret: clientSecret, RedirectURL: redirect, Name: name, AllowHTTP: allowHTTP == "true"})
		if err != nil {
			return err
		}
		api.OIDC = auth
	}
	aiEndpoint, _ := secret("AI_ENDPOINT", "")
	aiKey, _ := secret("AI_API_KEY", "")
	aiModel, _ := secret("AI_MODEL", "")
	aiHTTP, _ := secret("AI_ALLOW_HTTP", "false")
	aiBackend, _ := secret("AI_AGENT_BACKEND", "legacy")
	suggestions, err := service.NewSuggestions(repo, tasks, service.AIConfig{Endpoint: aiEndpoint, APIKey: aiKey, Model: aiModel, AllowHTTP: aiHTTP == "true", AgentBackend: aiBackend})
	if err != nil {
		return err
	}
	api.Suggestions = suggestions
	if aiBackend == "agentscope" {
		runnerSocket, _ := secret("AGENTSCOPE_RUNNER_SOCKET", "/run/drop-ai-runner/control.sock")
		gatewaySocket, _ := secret("AGENTSCOPE_GATEWAY_SOCKET", "/run/drop-ai-tools/control.sock")
		runner, err := service.NewAgentRunner(runnerSocket, gatewaySocket)
		if err != nil {
			return err
		}
		defer runner.Close()
		suggestions.Runner = runner
	}
	codeSocket, _ := secret("CODE_EXECUTOR_SOCKET", "")
	if codeSocket != "" {
		executor, err := service.NewCodeExecutor(codeSocket)
		if err != nil {
			return err
		}
		suggestions.Code = executor
	}
	go suggestions.Run(ctx)
	go suggestions.RunDiagnosis(ctx)
	go suggestions.RunVerifications(ctx)
	go suggestions.RunInvestigations(ctx)
	go suggestions.RunCodeRepairs(ctx)
	listen, _ := secret("HTTP_LISTEN", ":8080")
	server := &http.Server{Addr: listen, Handler: api.Router(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 20 * time.Second, IdleTimeout: 75 * time.Second, MaxHeaderBytes: 16 << 10}
	errCh := make(chan error, 1)
	go func() {
		slog.Info("API listening", "stage", "startup", "minimum_schema_version", repository.MinimumSchemaVersion)
		errCh <- server.ListenAndServe()
	}()
	select {
	case err := <-errCh:
		if !errors.Is(err, http.ErrServerClosed) {
			return err
		}
	case <-ctx.Done():
		shutdown, done := context.WithTimeout(context.Background(), 10*time.Second)
		defer done()
		return server.Shutdown(shutdown)
	}
	return nil
}
func main() {
	if err := run(); err != nil {
		slog.Error("API startup failed", "stage", "startup", "error_code", "CONFIGURATION_ERROR")
		os.Exit(1)
	}
}
