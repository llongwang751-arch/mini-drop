package config

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Principal struct {
	ID           string   `json:"id"`
	APIKey       string   `json:"api_key"`
	Roles        []string `json:"roles"`
	AgentIDs     []string `json:"agent_ids"`
	ServiceIDs   []string `json:"service_ids"`
	Environments []string `json:"environments"`
}

type Config struct {
	ListenAddr                 string
	DiagnosticAIGRPCAddress    string
	DiagnosticAIGRPCServerName string
	ControlGRPCAddress         string
	ControlGRPCToken           string
	ControlGRPCTimeoutMS       int
	ControlGRPCTLS             bool
	ControlGRPCCAFile          string
	ControlGRPCClientCertFile  string
	ControlGRPCClientKeyFile   string
	ControlGRPCServerName      string
	ProcessSnapshotMaxAgeSec   int
	DatabaseURL                string
	AuthEnabled                bool
	APIKey                     string
	Principals                 []Principal
	MinIOEndpoint              string
	MinIOAgentEndpoint         string
	MinIOAccessKey             string
	MinIOSecretKey             string
	MinIOBucket                string
	MinIOSecure                bool
	MinIOAgentSecure           bool
	MinIOUploadAuthTTLSeconds  int
}

func Load() (Config, error) {
	authEnabled := truthy(os.Getenv("MINI_DROP_API_AUTH_ENABLED"))
	apiKey := strings.TrimSpace(os.Getenv("MINI_DROP_API_KEY"))
	if authEnabled && apiKey == "" {
		if strings.TrimSpace(os.Getenv("MINI_DROP_API_PRINCIPALS_JSON")) == "" {
			return Config{}, fmt.Errorf("MINI_DROP_API_KEY or MINI_DROP_API_PRINCIPALS_JSON is required when API auth is enabled")
		}
	}
	principals, err := loadPrincipals(apiKey)
	if err != nil {
		return Config{}, err
	}
	minioEndpoint := env("MINIO_ENDPOINT", "minio:9000")
	minioSecure := truthy(os.Getenv("MINIO_SECURE"))
	cfg := Config{
		ListenAddr:                 env("MINI_DROP_API_LISTEN_ADDR", ":8080"),
		DiagnosticAIGRPCAddress:    env("MINI_DROP_DIAGNOSTIC_AI_GRPC_ADDRESS", "diagnosis-worker:50061"),
		DiagnosticAIGRPCServerName: strings.TrimSpace(os.Getenv("MINI_DROP_DIAGNOSTIC_AI_GRPC_SERVER_NAME")),
		ControlGRPCAddress:         env("MINI_DROP_CONTROL_GRPC_ADDRESS", "control-plane:50051"),
		ControlGRPCToken:           strings.TrimSpace(os.Getenv("MINI_DROP_GRPC_TOKEN")),
		ControlGRPCTimeoutMS:       envInt("MINI_DROP_CONTROL_GRPC_TIMEOUT_MS", 3000),
		ControlGRPCTLS:             truthy(os.Getenv("MINI_DROP_CONTROL_GRPC_TLS")),
		ControlGRPCCAFile:          strings.TrimSpace(os.Getenv("MINI_DROP_CONTROL_GRPC_CA_FILE")),
		ControlGRPCClientCertFile:  strings.TrimSpace(os.Getenv("MINI_DROP_CONTROL_GRPC_CLIENT_CERT_FILE")),
		ControlGRPCClientKeyFile:   strings.TrimSpace(os.Getenv("MINI_DROP_CONTROL_GRPC_CLIENT_KEY_FILE")),
		ControlGRPCServerName:      strings.TrimSpace(os.Getenv("MINI_DROP_CONTROL_GRPC_SERVER_NAME")),
		ProcessSnapshotMaxAgeSec:   envInt("MINI_DROP_PROCESS_SNAPSHOT_MAX_AGE_SEC", 30),
		DatabaseURL:                normalizeDatabaseURL(env("DATABASE_URL", "postgresql://mini_drop:mini_drop@postgres:5432/mini_drop")),
		AuthEnabled:                authEnabled,
		APIKey:                     apiKey,
		Principals:                 principals,
		MinIOEndpoint:              minioEndpoint,
		MinIOAgentEndpoint:         env("MINIO_AGENT_ENDPOINT", minioEndpoint),
		MinIOAccessKey:             env("MINIO_ACCESS_KEY", "mini_drop"),
		MinIOSecretKey:             env("MINIO_SECRET_KEY", "mini_drop_secret"),
		MinIOBucket:                env("MINIO_BUCKET", "mini-drop"),
		MinIOSecure:                minioSecure,
		MinIOAgentSecure:           truthyDefault(os.Getenv("MINIO_AGENT_SECURE"), minioSecure),
		MinIOUploadAuthTTLSeconds:  envInt("MINI_DROP_UPLOAD_AUTH_TTL_SEC", 1800),
	}
	if err := validateProduction(cfg); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

// validateProduction fails closed when MINI_DROP_ENV=production: the API must
// not start with the development-admin fallback or default object-store
// credentials (assessment §3.4).
func validateProduction(cfg Config) error {
	if strings.ToLower(strings.TrimSpace(os.Getenv("MINI_DROP_ENV"))) != "production" {
		return nil
	}
	if !cfg.AuthEnabled {
		return fmt.Errorf("production requires MINI_DROP_API_AUTH_ENABLED=true (no development principal fallback)")
	}
	if cfg.MinIOAccessKey == "mini_drop" || cfg.MinIOSecretKey == "mini_drop_secret" {
		return fmt.Errorf("production forbids default MinIO credentials")
	}
	if strings.TrimSpace(os.Getenv("MINIO_AGENT_ENDPOINT")) == "" {
		return fmt.Errorf("production requires an explicit Agent-reachable MINIO_AGENT_ENDPOINT")
	}
	if !cfg.ControlGRPCTLS {
		return fmt.Errorf("production requires TLS for the Go API to C++ control-plane hop")
	}
	if cfg.ControlGRPCCAFile == "" || cfg.ControlGRPCClientCertFile == "" || cfg.ControlGRPCClientKeyFile == "" {
		return fmt.Errorf("production requires CA, client certificate and client key for control-plane mTLS")
	}
	return nil
}

func loadPrincipals(fallbackAPIKey string) ([]Principal, error) {
	raw := strings.TrimSpace(os.Getenv("MINI_DROP_API_PRINCIPALS_JSON"))
	if raw == "" {
		if fallbackAPIKey == "" {
			return nil, nil
		}
		return []Principal{{
			ID: "legacy_admin", APIKey: fallbackAPIKey, Roles: []string{"admin"},
			AgentIDs: []string{"*"}, ServiceIDs: []string{"*"}, Environments: []string{"*"},
		}}, nil
	}
	var principals []Principal
	if err := json.Unmarshal([]byte(raw), &principals); err != nil {
		return nil, fmt.Errorf("MINI_DROP_API_PRINCIPALS_JSON must be a JSON array: %w", err)
	}
	seenID := map[string]bool{}
	for index := range principals {
		principal := &principals[index]
		principal.ID = strings.TrimSpace(principal.ID)
		principal.APIKey = strings.TrimSpace(principal.APIKey)
		if principal.ID == "" || principal.APIKey == "" || len(principal.Roles) == 0 {
			return nil, fmt.Errorf("principal %d requires id, api_key and roles", index)
		}
		if seenID[principal.ID] {
			return nil, fmt.Errorf("duplicate principal id %q", principal.ID)
		}
		seenID[principal.ID] = true
	}
	return principals, nil
}

func normalizeDatabaseURL(value string) string {
	return strings.Replace(value, "postgresql+psycopg://", "postgresql://", 1)
}

func env(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func truthy(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func truthyDefault(value string, fallback bool) bool {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return truthy(value)
}

func envInt(name string, fallback int) int {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed < 1 {
		return fallback
	}
	return parsed
}
