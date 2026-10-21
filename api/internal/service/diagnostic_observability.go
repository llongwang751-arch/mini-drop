// 可观测数据源由管理员离线登记，模型只能选择已经授权服务的固定信号类型。
// 本模块不接受模型提供的 URL、查询语句、文件路径或凭据；结果只保留诊断所需的有界结构字段。
package service

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"slices"
	"sort"
	"strings"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
)

var telemetryIdentifier = regexp.MustCompile(`^[a-zA-Z][a-zA-Z0-9_.-]{0,79}$`)

type TelemetryConnector struct {
	Kind      string            `json:"kind"`
	BaseURL   string            `json:"base_url"`
	AllowHTTP bool              `json:"allow_http,omitempty"`
	TokenFile string            `json:"token_file,omitempty"`
	TenantID  string            `json:"tenant_id,omitempty"`
	Queries   map[string]string `json:"queries,omitempty"`
	Selector  string            `json:"selector,omitempty"`
	Service   string            `json:"service,omitempty"`
	token     string
}

type TelemetryService struct {
	ID           string                        `json:"id"`
	Label        string                        `json:"label"`
	AgentID      string                        `json:"agent_id"`
	GroupID      *string                       `json:"group_id,omitempty"`
	Dependencies []string                      `json:"dependencies"`
	Connectors   map[string]TelemetryConnector `json:"connectors"`
}

type ObservabilityRegistry struct {
	Services map[string]TelemetryService
	Digest   string
	Client   *http.Client
}

func LoadObservabilityRegistry(path string) (*ObservabilityRegistry, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, errors.New("diagnostic observability configuration unavailable")
	}
	defer f.Close()
	info, statErr := f.Stat()
	data, readErr := io.ReadAll(io.LimitReader(f, (128<<10)+1))
	if statErr != nil || !info.Mode().IsRegular() || readErr != nil || len(data) > 128<<10 {
		return nil, errors.New("diagnostic observability configuration invalid")
	}
	return NewObservabilityRegistry(data)
}

// NewObservabilityRegistry 在启动时完整校验可信配置，错误仅返回固定说明，不回显 URL、路径或令牌。
func NewObservabilityRegistry(raw []byte) (*ObservabilityRegistry, error) {
	invalid := func() (*ObservabilityRegistry, error) {
		return nil, errors.New("invalid diagnostic observability configuration")
	}
	var config struct {
		SchemaVersion int                `json:"schema_version"`
		Services      []TelemetryService `json:"services"`
	}
	if len(raw) > 128<<10 || !decodeAgentJSON(raw, &config) || config.SchemaVersion != 1 || len(config.Services) > 32 {
		return invalid()
	}
	r := &ObservabilityRegistry{Services: map[string]TelemetryService{}, Digest: repository.DigestBytes(raw), Client: &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	for _, service := range config.Services {
		if !telemetryIdentifier.MatchString(service.ID) || service.AgentID == "" || len(service.AgentID) > 100 || len(service.Label) < 1 || len(service.Label) > 120 || strings.ContainsAny(service.Label, "\r\n\x00") || len(service.Dependencies) > 8 || len(service.Connectors) > 4 {
			return invalid()
		}
		if _, exists := r.Services[service.ID]; exists {
			return invalid()
		}
		for signal, connector := range service.Connectors {
			if !slices.Contains([]string{"metrics", "logs", "traces", "changes"}, signal) || !slices.Contains([]string{"prometheus", "loki", "tempo", "drop-adapter"}, connector.Kind) {
				return invalid()
			}
			if connector.Kind != "drop-adapter" && map[string]string{"metrics": "prometheus", "logs": "loki", "traces": "tempo"}[signal] != connector.Kind {
				return invalid()
			}
			u, err := url.Parse(connector.BaseURL)
			if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Scheme != "https" && !(connector.AllowHTTP && u.Scheme == "http")) || len(connector.BaseURL) > 500 || strings.ContainsAny(connector.TenantID, "\r\n\x00") || len(connector.TenantID) > 100 {
				return invalid()
			}
			if len(connector.Queries) > 3 || len(connector.Selector) > 1200 || len(connector.Service) > 80 {
				return invalid()
			}
			for name, query := range connector.Queries {
				if !slices.Contains([]string{"latency_p95_ms", "error_rate", "throughput_rps"}, name) || len(query) < 1 || len(query) > 1200 || strings.ContainsAny(query, "\r\n\x00") {
					return invalid()
				}
			}
			if connector.Kind == "prometheus" && len(connector.Queries) == 0 || connector.Kind == "loki" && connector.Selector == "" || connector.Kind == "tempo" && !telemetryIdentifier.MatchString(connector.Service) {
				return invalid()
			}
			if connector.TokenFile != "" {
				f, err := os.Open(connector.TokenFile)
				if err != nil {
					return invalid()
				}
				info, statErr := f.Stat()
				data, readErr := io.ReadAll(io.LimitReader(f, 4097))
				_ = f.Close()
				if statErr != nil || !info.Mode().IsRegular() || readErr != nil || len(data) > 4096 {
					return invalid()
				}
				connector.token = strings.TrimSpace(string(data))
				if connector.token == "" || strings.ContainsAny(connector.token, "\r\n\x00") {
					return invalid()
				}
			}
			service.Connectors[signal] = connector
		}
		r.Services[service.ID] = service
	}
	for _, service := range r.Services {
		for _, dependency := range service.Dependencies {
			if dependency == service.ID {
				return invalid()
			}
			if _, exists := r.Services[dependency]; !exists {
				return invalid()
			}
		}
	}
	return r, nil
}

func (s *Suggestions) validateTelemetryService(ctx context.Context, u domain.User, id, agentID string) error {
	return s.authorizeTelemetryService(ctx, u, id, agentID, true)
}

func (s *Suggestions) authorizeTelemetryService(ctx context.Context, u domain.User, id, agentID string, operate bool) error {
	if id == "" {
		return nil
	}
	if s.Observability == nil {
		return domain.Fail("CAPABILITY_UNAVAILABLE", "尚未登记业务可观测数据源")
	}
	service, exists := s.Observability.Services[id]
	if !exists || service.AgentID != agentID {
		return domain.Fail("FORBIDDEN", "数据源与当前目标不匹配")
	}
	if _, err := s.Repo.AuthorizedAgent(ctx, u, service.AgentID, operate); err != nil {
		return err
	}
	if service.GroupID == nil && !u.IsPlatformAdmin() {
		return domain.Fail("FORBIDDEN", "此数据源仅允许管理员使用")
	}
	return s.Repo.CheckGroup(ctx, u, service.GroupID, operate)
}

type TelemetryCatalogItem struct {
	ID          string   `json:"id"`
	Label       string   `json:"label"`
	SignalTypes []string `json:"signal_types"`
}

func (s *Suggestions) TelemetryCatalog(ctx context.Context, u domain.User, agentID string) []TelemetryCatalogItem {
	items := []TelemetryCatalogItem{}
	if s.Observability == nil {
		return items
	}
	for _, service := range s.Observability.Services {
		if s.validateTelemetryService(ctx, u, service.ID, agentID) != nil {
			continue
		}
		types := []string{}
		for signal := range service.Connectors {
			types = append(types, signal)
		}
		sort.Strings(types)
		items = append(items, TelemetryCatalogItem{service.ID, service.Label, types})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].ID < items[j].ID })
	return items
}

// telemetryScope 只沿管理员登记的一跳上下游展开；每个节点单独授权，授权不足的服务不进入模型上下文。
// S1 始终是绑定服务，后续引用是固定排序的别名，避免把真实服务名或主机信息发给外部模型。
func (s *Suggestions) telemetryScope(ctx context.Context, run DiagnosisRun) ([]TelemetryService, error) {
	items := []TelemetryService{}
	if run.TelemetryServiceID == "" {
		return items, nil
	}
	u, err := s.Repo.User(ctx, run.RequestedBy)
	if err != nil {
		return nil, err
	}
	var task domain.Task
	if err = s.Repo.Context(ctx).Where("id=?", run.TaskID).First(&task).Error; err != nil {
		return nil, err
	}
	if _, err = s.Repo.AuthorizeTaskWrite(ctx, u, task.ID); err != nil {
		return nil, err
	}
	if err = s.validateTelemetryService(ctx, u, run.TelemetryServiceID, diagnosticAgentID(task)); err != nil {
		return nil, err
	}
	for _, candidate := range s.Observability.registeredScope(run.TelemetryServiceID) {
		if s.validateTelemetryService(ctx, u, candidate.ID, candidate.AgentID) == nil {
			items = append(items, candidate)
		} else {
			// 保留别名位置：撤销S2授权不能让原S3服务被重编号为S2。
			// 空占位仅保留在控制面内，真实服务身份不会发给模型。
			items = append(items, TelemetryService{})
		}
	}
	return items, nil
}

func (r *ObservabilityRegistry) registeredScope(rootID string) []TelemetryService {
	if r == nil {
		return nil
	}
	root, exists := r.Services[rootID]
	if !exists {
		return nil
	}
	items := []TelemetryService{root}
	ids := append([]string{}, root.Dependencies...)
	for _, candidate := range r.Services {
		if slices.Contains(candidate.Dependencies, rootID) {
			ids = append(ids, candidate.ID)
		}
	}
	sort.Strings(ids)
	ids = slices.Compact(ids)
	for _, id := range ids {
		if len(items) >= 8 {
			break
		}
		items = append(items, r.Services[id])
	}
	return items
}

func (r *ObservabilityRegistry) fetch(ctx context.Context, c TelemetryConnector, path string, query url.Values) ([]byte, error) {
	u, err := url.Parse(c.BaseURL)
	if err != nil {
		return nil, domain.Fail("SIGNAL_SOURCE_INVALID", "数据源配置不可用")
	}
	u.Path = strings.TrimRight(u.Path, "/") + path
	u.RawQuery = query.Encode()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return nil, domain.Fail("SIGNAL_SOURCE_INVALID", "数据源请求无效")
	}
	request.Header.Set("Accept", "application/json")
	if c.token != "" {
		request.Header.Set("Authorization", "Bearer "+c.token)
	}
	if c.TenantID != "" {
		request.Header.Set("X-Scope-OrgID", c.TenantID)
	}
	response, err := r.Client.Do(request)
	if err != nil {
		return nil, domain.Fail("SIGNAL_SOURCE_UNAVAILABLE", "业务信号查询失败")
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, (256<<10)+1))
	if err != nil || len(data) > 256<<10 || response.StatusCode != http.StatusOK {
		return nil, domain.Fail("SIGNAL_SOURCE_UNAVAILABLE", "业务信号响应异常或超过预算")
	}
	return data, nil
}
