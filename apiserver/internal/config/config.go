package config

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
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
	ListenAddr           string
	LegacyAPIURL         *url.URL
	DatabaseURL          string
	AuthEnabled          bool
	APIKey               string
	Principals           []Principal
	InternalGatewayToken string
	MinIOEndpoint        string
	MinIOAccessKey       string
	MinIOSecretKey       string
	MinIOBucket          string
	MinIOSecure          bool
}

func Load() (Config, error) {
	rawUpstream := env("MINI_DROP_LEGACY_API_URL", "http://server:8191")
	upstream, err := url.Parse(rawUpstream)
	if err != nil || upstream.Scheme == "" || upstream.Host == "" {
		return Config{}, fmt.Errorf("MINI_DROP_LEGACY_API_URL must be an absolute URL")
	}
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
	cfg := Config{
		ListenAddr:           env("MINI_DROP_API_LISTEN_ADDR", ":8080"),
		LegacyAPIURL:         upstream,
		DatabaseURL:          normalizeDatabaseURL(env("DATABASE_URL", "postgresql://mini_drop:mini_drop@postgres:5432/mini_drop")),
		AuthEnabled:          authEnabled,
		APIKey:               apiKey,
		Principals:           principals,
		InternalGatewayToken: strings.TrimSpace(os.Getenv("MINI_DROP_INTERNAL_GATEWAY_TOKEN")),
		MinIOEndpoint:        env("MINIO_ENDPOINT", "minio:9000"),
		MinIOAccessKey:       env("MINIO_ACCESS_KEY", "mini_drop"),
		MinIOSecretKey:       env("MINIO_SECRET_KEY", "mini_drop_secret"),
		MinIOBucket:          env("MINIO_BUCKET", "mini-drop"),
		MinIOSecure:          truthy(os.Getenv("MINIO_SECURE")),
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
	if cfg.InternalGatewayToken == "" || cfg.InternalGatewayToken == "mini-drop-internal-dev" {
		return fmt.Errorf("production requires a non-default MINI_DROP_INTERNAL_GATEWAY_TOKEN")
	}
	if cfg.MinIOAccessKey == "mini_drop" || cfg.MinIOSecretKey == "mini_drop_secret" {
		return fmt.Errorf("production forbids default MinIO credentials")
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
