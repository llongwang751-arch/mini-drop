// 本文件验证配置优先级、秘密引用和脱敏哈希，不同来源覆盖时不应读取已被遮蔽的秘密文件。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package config

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func fixtureFile(t *testing.T, text string) string {
	t.Helper()
	file := filepath.Join(t.TempDir(), "imported-config-fixture.json")
	if err := os.WriteFile(file, []byte(text), 0600); err != nil {
		t.Fatal(err)
	}
	return file
}

func environmentFixture(values map[string]string) func(string) (string, bool) {
	return func(name string) (string, bool) { value, ok := values[name]; return value, ok }
}

func mustValue(t *testing.T, config *Config, name string) string {
	t.Helper()
	value, err := config.Value(name, "")
	if err != nil {
		t.Fatal(err)
	}
	return value
}

func TestLogicalPrecedenceAndLegacySecretFiles(t *testing.T) {
	secret := fixtureFile(t, "IMPORTED_DATABASE_SECRET\n")
	file := fixtureFile(t, `{"HTTP_LISTEN":":9001","S3_BUCKET":"from-file","AI_MODEL":"fixture-model","DATABASE_URL_FILE":`+quoted(secret)+`}`)
	loaded, err := Load([]string{"migrate", "--config", file, "--set", "HTTP_LISTEN=:9003"}, environmentFixture(map[string]string{
		"HTTP_LISTEN": ":9002", "S3_BUCKET": "from-env", "DATABASE_URL": "env-secret",
		"DATABASE_URL_FILE": secret,
	}))
	if err != nil {
		t.Fatal(err)
	}
	if !loaded.Migrate || mustValue(t, loaded, "HTTP_LISTEN") != ":9003" || mustValue(t, loaded, "S3_BUCKET") != "from-env" || mustValue(t, loaded, "AI_MODEL") != "fixture-model" || mustValue(t, loaded, "DATABASE_URL") != "IMPORTED_DATABASE_SECRET" || mustValue(t, loaded, "OIDC_ALLOW_HTTP") != "false" {
		t.Fatal("CLI/env/file/default or legacy secret-file precedence changed")
	}
}

func quoted(value string) string { data, _ := json.Marshal(value); return string(data) }

func TestHigherPrioritySettingDoesNotReadShadowedSecretReference(t *testing.T) {
	file := fixtureFile(t, `{"DATABASE_URL_FILE":"missing-imported-fixture-secret","HTTP_LISTEN_FILE":"missing-imported-setting"}`)
	loaded, err := Load([]string{"--config=" + file, "--set=HTTP_LISTEN=:9010"}, environmentFixture(map[string]string{"DATABASE_URL": "IMPORTED_ENV_SECRET"}))
	if err != nil || mustValue(t, loaded, "DATABASE_URL") != "IMPORTED_ENV_SECRET" || mustValue(t, loaded, "HTTP_LISTEN") != ":9010" {
		t.Fatal("A shadowed lower-priority file must not be opened")
	}
}

func TestRejectsUnsafeConfigurationWithoutEchoingInputs(t *testing.T) {
	for _, text := range []string{
		`{"UNKNOWN_KEY":"IMPORTED_TOKEN"}`,
		`{"DATABASE_URL":"postgres://IMPORTED_TOKEN"}`,
		`{"HTTP_LISTEN":5}`,
		`{"HTTP_LISTEN":null}`,
		`{"HTTP_LISTEN":":1","HTTP_LISTEN":":2"}`,
		`{"HTTP_LISTEN":"line\nbreak"}`,
		`{"HTTP_LISTEN":"bad\u0000value"}`,
		`{} {}`,
		`[]`,
		strings.Repeat(" ", maxBytes+1),
	} {
		_, err := Load([]string{"--config", fixtureFile(t, text)}, environmentFixture(nil))
		if !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "IMPORTED_TOKEN") {
			t.Fatal("Unsafe file was accepted or leaked its value")
		}
	}
	for _, args := range [][]string{
		{"--set", "UNKNOWN_KEY=IMPORTED_TOKEN"},
		{"--set", "ADMIN_PASSWORD=IMPORTED_TOKEN"},
		{"--set", "DATABASE_URL=IMPORTED_TOKEN"},
		{"--set", "HTTP_LISTEN=first", "--set", "HTTP_LISTEN=second"},
		{"--set", "HTTP_LISTEN=bad\nvalue"},
		{"--unknown=IMPORTED_TOKEN"},
		{"--config"},
		{"arbitrary-command"},
	} {
		_, err := Load(args, environmentFixture(nil))
		if !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "IMPORTED_TOKEN") {
			t.Fatal("Unsafe CLI was accepted or leaked its value")
		}
	}
	_, err := Load(nil, environmentFixture(map[string]string{"PUBLIC_URL": "bad\rvalue"}))
	if !errors.Is(err, ErrInvalid) {
		t.Fatal("Control characters from the environment were accepted")
	}
}

func TestSecretReferenceCLIAndRedactedHashNeverDependOnSecretBytes(t *testing.T) {
	first := fixtureFile(t, "IMPORTED_SECRET_ONE")
	second := fixtureFile(t, "IMPORTED_SECRET_TWO")
	load := func(path string) *Config {
		value, err := Load([]string{"--set", "AI_API_KEY_FILE=" + path}, environmentFixture(map[string]string{"PUBLIC_URL": "https://user:IMPORTED_SECRET@example.test/path?token=IMPORTED_SECRET"}))
		if err != nil {
			t.Fatal(err)
		}
		return value
	}
	a, b := load(first), load(second)
	if mustValue(t, a, "AI_API_KEY") != "IMPORTED_SECRET_ONE" || a.Hash() != b.Hash() || len(a.Hash()) != 64 {
		t.Fatal("Secret reference resolution or safe hash changed")
	}
	encoded, err := json.Marshal(a.Effective())
	if err != nil || strings.Contains(string(encoded), "IMPORTED_SECRET") || strings.Contains(string(encoded), first) || strings.Contains(string(encoded), "user:") {
		t.Fatal("Effective settings disclosed secret values, query strings or paths")
	}
	c, err := Load([]string{"--set", "S3_BUCKET=another-bucket"}, environmentFixture(nil))
	if err != nil || c.Hash() == a.Hash() {
		t.Fatal("Safe public configuration changes must affect the hash")
	}
}

func TestEffectiveConfigMatchesResolvedDefaultsAndDerivedCallback(t *testing.T) {
	loaded, err := Load([]string{"--set", "PUBLIC_URL=https://drop.example.test", "--set", "HTTP_LISTEN="}, environmentFixture(nil))
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(loaded.Effective())
	var values map[string]string
	if json.Unmarshal(encoded, &values) != nil || values["HTTP_LISTEN"] != ":8080" || values["PUBLIC_URL"] != "https://drop.example.test" || mustValue(t, loaded, "OIDC_REDIRECT_URL") != "https://drop.example.test/api/v1/auth/oidc/callback" {
		t.Fatal("Effective defaults did not match runtime resolution")
	}
	changed, err := Load([]string{"--set", "HTTP_LISTEN=:8099"}, environmentFixture(nil))
	if err != nil || loaded.Hash() == changed.Hash() {
		t.Fatal("Safe endpoint changes must participate in config hash")
	}
}
