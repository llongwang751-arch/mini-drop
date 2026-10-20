// Package config resolves only the API's registered settings. Each service owns
// its configuration; credentials are never exported through effective settings.
package config

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"os"
	"regexp"
	"strings"
	"unicode"
)

const maxBytes = 64 << 10

var ErrInvalid = errors.New("invalid API configuration")

type definition struct {
	value     string
	secret    bool
	reference bool
	endpoint  bool
}

var definitions = map[string]definition{
	"DATABASE_URL":                {secret: true},
	"MIGRATIONS_DIR":              {value: "/app/db/migrations", reference: true},
	"TLS_CA_FILE":                 {reference: true},
	"TLS_CERT_FILE":               {reference: true},
	"TLS_KEY_FILE":                {reference: true},
	"S3_ACCESS_KEY":               {secret: true},
	"S3_SECRET_KEY":               {secret: true},
	"ADMIN_USERNAME":              {secret: true},
	"ADMIN_PASSWORD":              {secret: true},
	"DROP_SERVER_ADDRESS":         {value: "drop-server:50051", endpoint: true},
	"OTEL_EXPORTER_OTLP_ENDPOINT": {endpoint: true},
	"S3_ENDPOINT":                 {value: "minio:9000", endpoint: true},
	"S3_BUCKET":                   {value: "drop-artifacts"},
	"PUBLIC_URL":                  {value: "http://localhost", endpoint: true},
	"TASK_KINDS_FILE":             {value: "/app/contracts/task-kinds.json", reference: true},
	"OIDC_ISSUER":                 {endpoint: true},
	"OIDC_CLIENT_ID":              {},
	"OIDC_CLIENT_SECRET":          {secret: true},
	"OIDC_REDIRECT_URL":           {endpoint: true},
	"OIDC_PROVIDER_NAME":          {value: "企业账号"},
	"OIDC_ALLOW_HTTP":             {value: "false"},
	"AI_ENDPOINT":                 {endpoint: true},
	"AI_API_KEY":                  {secret: true},
	"AI_MODEL":                    {},
	"AI_AGENT_BACKEND":            {value: "legacy"},
	"AGENTSCOPE_RUNNER_SOCKET":    {reference: true},
	"AGENTSCOPE_GATEWAY_SOCKET":   {reference: true},
	"CODE_EXECUTOR_SOCKET":        {},
	"AI_ALLOW_HTTP":               {value: "false"},
	"HTTP_LISTEN":                 {value: ":8080", endpoint: true},
}

type Config struct {
	values  map[string]string
	Migrate bool
}

// SafeValues cannot be constructed from arbitrary raw settings by another
// package. Its JSON and hash contain redaction markers, never secret digests.
type SafeValues struct{ values map[string]string }

func (s SafeValues) MarshalJSON() ([]byte, error) { return json.Marshal(s.values) }

func valid(value string) bool {
	return len(value) <= maxBytes && !strings.ContainsFunc(value, unicode.IsControl)
}

func logicalKey(key string) (string, bool, bool) {
	if _, ok := definitions[key]; ok {
		return key, false, true
	}
	if base, ok := strings.CutSuffix(key, "_FILE"); ok {
		if _, exists := definitions[base]; exists {
			return base, true, true
		}
	}
	return "", false, false
}

func boundedFile(name string) (data []byte, err error) {
	if name == "" || !valid(name) {
		return nil, ErrInvalid
	}
	file, err := openRegular(name)
	if err != nil {
		return nil, ErrInvalid
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > maxBytes {
		return nil, ErrInvalid
	}
	data, err = io.ReadAll(io.LimitReader(file, maxBytes+1))
	if err != nil || len(data) > maxBytes {
		return nil, ErrInvalid
	}
	return data, nil
}

func fileSettings(name string) (map[string]string, error) {
	data, err := boundedFile(name)
	if err != nil {
		return nil, ErrInvalid
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	token, err := decoder.Token()
	if err != nil || token != json.Delim('{') {
		return nil, ErrInvalid
	}
	values := map[string]string{}
	for decoder.More() {
		token, err := decoder.Token()
		key, ok := token.(string)
		if err != nil || !ok {
			return nil, ErrInvalid
		}
		if _, duplicate := values[key]; duplicate {
			return nil, ErrInvalid
		}
		base, reference, known := logicalKey(key)
		var raw any
		if !known || decoder.Decode(&raw) != nil {
			return nil, ErrInvalid
		}
		value, stringValue := raw.(string)
		if !stringValue || !valid(value) || (definitions[base].secret && !reference) {
			return nil, ErrInvalid
		}
		values[key] = value
	}
	if token, err = decoder.Token(); err != nil || token != json.Delim('}') {
		return nil, ErrInvalid
	}
	if decoder.Decode(new(any)) != io.EOF {
		return nil, ErrInvalid
	}
	return values, nil
}

// Load applies CLI > environment > JSON file > defaults. Within one environment
// or file layer NAME_FILE retains the existing precedence over NAME. Empty
// environment values retain legacy unset behavior; explicit CLI values may empty
// an optional setting. Secret contents are accepted only from env or _FILE.
func Load(args []string, lookup func(string) (string, bool)) (*Config, error) {
	if lookup == nil {
		lookup = os.LookupEnv
	}
	if len(args) > 128 || len(strings.Join(args, "")) > maxBytes {
		return nil, ErrInvalid
	}
	result := &Config{values: map[string]string{}}
	cli, seen := map[string]string{}, map[string]bool{}
	filename := ""
	for index := 0; index < len(args); index++ {
		argument := args[index]
		if !valid(argument) {
			return nil, ErrInvalid
		}
		if argument == "migrate" && index == 0 {
			result.Migrate = true
			continue
		}
		flag, value, hasValue := strings.Cut(argument, "=")
		if flag != "--config" && flag != "--set" {
			return nil, ErrInvalid
		}
		if !hasValue {
			index++
			if index >= len(args) || !valid(args[index]) {
				return nil, ErrInvalid
			}
			value = args[index]
		}
		if flag == "--config" {
			if filename != "" || value == "" {
				return nil, ErrInvalid
			}
			filename = value
			continue
		}
		key, setting, ok := strings.Cut(value, "=")
		base, reference, known := logicalKey(key)
		if !ok || !known || seen[base] || !valid(setting) || (definitions[base].secret && !reference) {
			return nil, ErrInvalid
		}
		seen[base] = true
		cli[key] = setting
	}
	fileValues := map[string]string{}
	if filename != "" {
		var err error
		fileValues, err = fileSettings(filename)
		if err != nil {
			return nil, ErrInvalid
		}
	}
	for key, spec := range definitions {
		value := spec.value
		selectedPath := ""
		for _, layer := range []map[string]string{fileValues, environment(key, lookup), cli} {
			if path := layer[key+"_FILE"]; path != "" {
				if !valid(path) {
					return nil, ErrInvalid
				}
				selectedPath = path
			} else if selected, exists := layer[key]; exists {
				value = selected
				selectedPath = ""
			}
			if !valid(value) {
				return nil, ErrInvalid
			}
		}
		if selectedPath != "" {
			data, err := boundedFile(selectedPath)
			if err != nil {
				return nil, ErrInvalid
			}
			value = strings.TrimSpace(string(data))
			if !valid(value) {
				return nil, ErrInvalid
			}
		}
		if value == "" && spec.value != "" {
			value = spec.value
		}
		result.values[key] = value
	}
	if result.values["OIDC_REDIRECT_URL"] == "" {
		result.values["OIDC_REDIRECT_URL"] = result.values["PUBLIC_URL"] + "/api/v1/auth/oidc/callback"
	}
	return result, nil
}

func environment(key string, lookup func(string) (string, bool)) map[string]string {
	values := map[string]string{}
	for _, name := range []string{key, key + "_FILE"} {
		if value, exists := lookup(name); exists && value != "" {
			values[name] = value
		}
	}
	return values
}

func (c *Config) Value(name, fallback string) (string, error) {
	value, known := c.values[name]
	if !known {
		return "", ErrInvalid
	}
	if value == "" {
		return fallback, nil
	}
	return value, nil
}

func (c *Config) Effective() SafeValues {
	values := map[string]string{}
	for key, value := range c.values {
		spec := definitions[key]
		if value == "" {
			values[key] = "<unset>"
		} else if spec.endpoint {
			values[key] = safeEndpoint(value)
		} else if spec.secret || spec.reference {
			// Secret values and reference paths are never hashed or emitted.
			values[key] = "<configured>"
		} else if _, err := url.Parse(value); err != nil || strings.ContainsAny(value, "?@#") {
			values[key] = "<configured>"
		} else {
			values[key] = value
		}
	}
	return SafeValues{values: values}
}

var endpointAddress = regexp.MustCompile(`^[A-Za-z0-9.\[\]:-]{1,255}$`)

func safeEndpoint(value string) string {
	if endpointAddress.MatchString(value) {
		return value
	}
	parsed, err := url.Parse(value)
	if err == nil && (parsed.Scheme == "http" || parsed.Scheme == "https") && parsed.Host != "" && endpointAddress.MatchString(parsed.Host) {
		return parsed.Scheme + "://" + parsed.Host
	}
	return "<configured>"
}

func (c *Config) Hash() string {
	data, _ := c.Effective().MarshalJSON()
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}
