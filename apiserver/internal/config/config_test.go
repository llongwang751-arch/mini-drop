package config

import (
	"testing"
)

func TestValidateProductionAllowsDevelopmentEnv(t *testing.T) {
	t.Setenv("MINI_DROP_ENV", "development")
	cfg := Config{AuthEnabled: false, MinIOAccessKey: "mini_drop", MinIOSecretKey: "mini_drop_secret"}
	if err := validateProduction(cfg); err != nil {
		t.Fatalf("development env must not fail closed: %v", err)
	}
}

func TestValidateProductionRequiresAuth(t *testing.T) {
	t.Setenv("MINI_DROP_ENV", "production")
	cfg := Config{AuthEnabled: false, MinIOAccessKey: "real", MinIOSecretKey: "real"}
	if err := validateProduction(cfg); err == nil {
		t.Fatal("production must reject disabled auth (dev principal fallback)")
	}
}

func TestValidateProductionForbidsDefaultMinIO(t *testing.T) {
	t.Setenv("MINI_DROP_ENV", "production")
	cfg := Config{AuthEnabled: true, InternalGatewayToken: "internal-secret", MinIOAccessKey: "mini_drop", MinIOSecretKey: "mini_drop_secret"}
	if err := validateProduction(cfg); err == nil {
		t.Fatal("production must reject default MinIO credentials")
	}
}

func TestValidateProductionAcceptsSecureConfig(t *testing.T) {
	t.Setenv("MINI_DROP_ENV", "production")
	cfg := Config{AuthEnabled: true, InternalGatewayToken: "internal-secret", MinIOAccessKey: "real", MinIOSecretKey: "real"}
	if err := validateProduction(cfg); err != nil {
		t.Fatalf("secure production config must pass: %v", err)
	}
}

func TestValidateProductionRequiresIndependentGatewayToken(t *testing.T) {
	t.Setenv("MINI_DROP_ENV", "production")
	for _, token := range []string{"", "mini-drop-internal-dev"} {
		cfg := Config{AuthEnabled: true, InternalGatewayToken: token, MinIOAccessKey: "real", MinIOSecretKey: "real"}
		if err := validateProduction(cfg); err == nil {
			t.Fatalf("production must reject gateway token %q", token)
		}
	}
}
