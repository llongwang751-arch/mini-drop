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
	cfg := Config{AuthEnabled: true, DiagnosticAIGRPCAddress: "diagnosis-worker:50061", MinIOAccessKey: "mini_drop", MinIOSecretKey: "mini_drop_secret"}
	if err := validateProduction(cfg); err == nil {
		t.Fatal("production must reject default MinIO credentials")
	}
}

func TestValidateProductionAcceptsSecureConfig(t *testing.T) {
	t.Setenv("MINI_DROP_ENV", "production")
	t.Setenv("MINIO_AGENT_ENDPOINT", "storage.example.test:9443")
	cfg := Config{
		AuthEnabled: true, DiagnosticAIGRPCAddress: "diagnosis-worker:50061",
		MinIOAccessKey: "real", MinIOSecretKey: "real",
		ControlGRPCTLS: true, ControlGRPCCAFile: "/certs/ca.crt",
		ControlGRPCClientCertFile: "/certs/client.crt",
		ControlGRPCClientKeyFile:  "/certs/client.key",
	}
	if err := validateProduction(cfg); err != nil {
		t.Fatalf("secure production config must pass: %v", err)
	}
}

func TestValidateProductionRequiresAgentReachableStorageEndpoint(t *testing.T) {
	t.Setenv("MINI_DROP_ENV", "production")
	t.Setenv("MINIO_AGENT_ENDPOINT", "")
	cfg := Config{
		AuthEnabled: true, DiagnosticAIGRPCAddress: "diagnosis-worker:50061",
		MinIOAccessKey: "real", MinIOSecretKey: "real",
		ControlGRPCTLS: true, ControlGRPCCAFile: "/certs/ca.crt",
		ControlGRPCClientCertFile: "/certs/client.crt",
		ControlGRPCClientKeyFile:  "/certs/client.key",
	}
	if err := validateProduction(cfg); err == nil {
		t.Fatal("production must reject an implicit internal-only storage endpoint")
	}
}

func TestValidateProductionRequiresControlPlaneMutualTLS(t *testing.T) {
	t.Setenv("MINI_DROP_ENV", "production")
	cfg := Config{
		AuthEnabled: true, DiagnosticAIGRPCAddress: "diagnosis-worker:50061",
		MinIOAccessKey: "real", MinIOSecretKey: "real",
	}
	if err := validateProduction(cfg); err == nil {
		t.Fatal("production must reject an insecure Go to C++ control-plane hop")
	}
}
