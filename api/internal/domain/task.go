package domain

import (
	"encoding/json"
	"fmt"
	"time"
)

type Error struct {
	Code      string         `json:"code"`
	Message   string         `json:"message"`
	Retryable bool           `json:"retryable"`
	Details   map[string]any `json:"details"`
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }
func Fail(code, message string) *Error {
	return &Error{Code: code, Message: message, Details: map[string]any{}}
}

type User struct {
	ID           string    `json:"id" gorm:"primaryKey"`
	Username     string    `json:"username"`
	DisplayName  string    `json:"display_name"`
	Role         string    `json:"role"`
	PasswordHash string    `json:"-"`
	CreatedAt    time.Time `json:"created_at"`
}

func (u User) IsPlatformAdmin() bool     { return u.Role == "admin" || u.Role == "platform_admin" }
func (u User) CanRead(owner string) bool { return u.IsPlatformAdmin() || u.ID == owner }
func (u User) CanWrite() bool {
	return u.IsPlatformAdmin() || u.Role == "operator" || u.Role == "group_admin"
}

type Parameters struct {
	PID             int            `json:"pid,omitempty"`
	DurationSeconds int            `json:"duration_seconds,omitempty"`
	FrequencyHz     int            `json:"frequency_hz,omitempty"`
	Scope           string         `json:"scope,omitempty"`
	Target          *Target        `json:"target,omitempty"`
	Budget          *Budget        `json:"budget,omitempty"`
	Options         map[string]any `json:"options,omitempty"`
	Source          string         `json:"source,omitempty"`
	Traceparent     string         `json:"traceparent,omitempty"`
}
type CreateTask struct {
	Name          string       `json:"name"`
	Kind          string       `json:"kind"`
	Priority      int          `json:"priority"`
	AgentID       string       `json:"target_agent_id"`
	Parameters    Parameters   `json:"parameters"`
	CollapsedText string       `json:"collapsed_text,omitempty"`
	GroupID       *string      `json:"group_id,omitempty"`
	Visibility    string       `json:"visibility,omitempty"`
	Import        *ImportInput `json:"import,omitempty"`
}

func (c *CreateTask) Normalize() {
	if c.Name == "" {
		c.Name = c.Kind
	}
	k, _ := Kind(c.Kind)
	if c.Parameters.DurationSeconds == 0 {
		c.Parameters.DurationSeconds = k.DefaultDuration
	}
	if c.Parameters.FrequencyHz == 0 {
		c.Parameters.FrequencyHz = k.DefaultFrequency
	}
	if c.Parameters.Scope == "" {
		c.Parameters.Scope = "process"
	}
	if c.Parameters.Target != nil {
		c.Parameters.Scope = c.Parameters.Target.Type
		if c.Parameters.Target.PID > 0 {
			c.Parameters.PID = c.Parameters.Target.PID
		}
	}
	if c.Parameters.Scope == "process" && c.Parameters.Target == nil && len(k.Targets) == 1 && k.Targets[0] == "host" {
		c.Parameters.Scope = "host"
		c.Parameters.Target = &Target{Type: "host"}
	}
	if c.Visibility == "" {
		c.Visibility = "private"
	}
}
func (c CreateTask) Validate() *Error {
	if c.Priority < -10 || c.Priority > 10 {
		return Fail("INVALID_PARAMETERS", "队列优先级须为 -10 至 10")
	}
	k, ok := Kind(c.Kind)
	if !ok || c.Kind == "COMPOSITE" {
		return Fail("UNSUPPORTED_KIND", "当前不支持该任务类型")
	}
	if len(c.Name) > 120 {
		return Fail("INVALID_PARAMETERS", "任务名称过长")
	}
	if c.Parameters.Source != "" || c.Parameters.Traceparent != "" {
		return Fail("INVALID_PARAMETERS", "来源由服务器判定")
	}
	if c.Visibility != "" && c.Visibility != "private" && c.Visibility != "group" {
		return Fail("INVALID_PARAMETERS", "任务可见性无效")
	}
	if c.Visibility == "group" && c.GroupID == nil {
		return Fail("INVALID_PARAMETERS", "组内可见任务必须指定group_id")
	}
	if c.Import != nil {
		if c.AgentID != "" || c.CollapsedText != "" {
			return Fail("INVALID_PARAMETERS", "导入与Agent目标不能混用")
		}
		if err := ValidateImport(k, *c.Import); err != nil {
			return err
		}
		return ValidateKindParameters(c.Kind, c.Parameters)
	}
	if c.Kind == "IMPORT_COLLAPSED" {
		if c.CollapsedText == "" || len(c.CollapsedText) > 2<<20 {
			return Fail("INVALID_PARAMETERS", "导入文本须为 1 字节至 2 MiB")
		}
		return nil
	}
	if err := ValidateKindParameters(c.Kind, c.Parameters); err != nil {
		return err
	}
	if len(k.Targets) > 0 {
		allowed := false
		for _, target := range k.Targets {
			allowed = allowed || target == c.Parameters.Scope
		}
		if !allowed {
			return Fail("INVALID_PARAMETERS", "该TaskKind不支持此目标范围")
		}
	}
	if c.Parameters.Scope == "container" && c.Parameters.Target != nil && c.Parameters.Target.ContainerID != "" && c.AgentID != "" {
		return nil
	}
	if c.Kind != "PERF_CPU" && c.Kind != "PROC_CPU" {
		if c.AgentID == "" || !k.AgentRunner {
			return Fail("INVALID_PARAMETERS", "必须指定采集Agent")
		}
		if c.Parameters.Scope == "host" {
			return nil
		}
		if c.Parameters.Scope == "container" && c.Parameters.Target != nil && c.Parameters.Target.ContainerID != "" {
			return nil
		}
		if k.RequiresPID && c.Parameters.PID < 2 {
			return Fail("INVALID_PARAMETERS", "必须指定合法PID")
		}
		return nil
	}
	maxFreq := 199
	if c.Kind == "PROC_CPU" {
		maxFreq = 10
	}
	if c.AgentID == "" || c.Parameters.PID < 2 || c.Parameters.DurationSeconds < 1 || c.Parameters.DurationSeconds > 60 || c.Parameters.FrequencyHz < 1 || c.Parameters.FrequencyHz > maxFreq || (c.Parameters.Scope != "" && c.Parameters.Scope != "process") {
		return Fail("INVALID_PARAMETERS", "PID、采样时长、频率或采样范围无效")
	}
	if c.CollapsedText != "" {
		return Fail("INVALID_PARAMETERS", "采集任务不能同时导入文本")
	}
	return nil
}

type Task struct {
	Permissions      *TaskPermissions `json:"permissions,omitempty" gorm:"-"`
	ID               int64            `json:"id,string" gorm:"primaryKey"`
	Name             string           `json:"name"`
	Kind             string           `json:"kind"`
	Priority         int              `json:"priority"`
	AgentID          *string          `json:"target_agent_id" gorm:"column:target_agent_id"`
	Parameters       json.RawMessage  `json:"params" gorm:"column:params;type:jsonb"`
	CollectionStatus string           `json:"collection_status"`
	AnalysisStatus   string           `json:"analysis_status"`
	OwnerID          string           `json:"creator_id" gorm:"column:creator_id"`
	GroupID          *string          `json:"group_id"`
	Visibility       string           `json:"visibility" gorm:"default:private"`
	RequestID        string           `json:"request_id"`
	TraceParent      string           `json:"-"`
	DeletedAt        *time.Time       `json:"deleted_at,omitempty"`
	IdempotencyKey   string           `json:"-"`
	RequestHash      string           `json:"-"`
	ParentTaskID     *int64           `json:"parent_task_id,omitempty,string"`
	DeadlineAt       *time.Time       `json:"collection_deadline,omitempty" gorm:"column:collection_deadline"`
	ErrorCode        *string          `json:"error_code,omitempty"`
	ErrorMessage     *string          `json:"error_message,omitempty"`
	Version          int64            `json:"version"`
	CreatedAt        time.Time        `json:"created_at"`
	UpdatedAt        time.Time        `json:"updated_at"`
}

func (t Task) Terminal() bool {
	return t.CollectionStatus == "FAILED" || t.CollectionStatus == "CANCELED" || (t.CollectionStatus == "COLLECTED" && (t.AnalysisStatus == "SUCCESS" || t.AnalysisStatus == "FAILED"))
}

type Attempt struct {
	ID             int64           `json:"id,string" gorm:"primaryKey"`
	TaskID         int64           `json:"task_id,string"`
	Attempt        int             `json:"attempt"`
	AgentID        *string         `json:"agent_id"`
	Status         string          `json:"status"`
	RunnerVersion  string          `json:"runner_version"`
	RawObjectKey   string          `json:"-"`
	RawUploadURL   string          `json:"-" gorm:"column:raw_upload_url"`
	StartedAt      *time.Time      `json:"started_at"`
	FinishedAt     *time.Time      `json:"finished_at"`
	ExitCode       *int            `json:"exit_code"`
	ErrorCode      *string         `json:"error_code"`
	ErrorMessage   *string         `json:"error_message"`
	CreatedAt      time.Time       `json:"created_at"`
	RunnerMetadata json.RawMessage `json:"runner_metadata" gorm:"type:jsonb;default:'{}'"`
	UploadTargets  json.RawMessage `json:"-" gorm:"type:jsonb;default:'[]'"`
}

func (Attempt) TableName() string { return "task_attempts" }

type Agent struct {
	ID                string          `json:"id" gorm:"primaryKey"`
	Hostname          string          `json:"hostname"`
	IP                string          `json:"ip"`
	Version           string          `json:"version"`
	Platform          string          `json:"platform"`
	Status            string          `json:"status"`
	Capabilities      json.RawMessage `json:"capabilities" gorm:"type:jsonb"`
	CapabilityDetails json.RawMessage `json:"capability_details" gorm:"type:jsonb;default:'{}'"`
	Labels            json.RawMessage `json:"labels" gorm:"type:jsonb"`
	Resources         json.RawMessage `json:"resources" gorm:"type:jsonb"`
	CPUPercent        float64         `json:"cpu_percent"`
	MemoryPercent     float64         `json:"memory_percent"`
	LoadAverage       float64         `json:"load_average"`
	LastSeenAt        time.Time       `json:"last_seen_at"`
	CreatedAt         time.Time       `json:"created_at"`
}

func (a Agent) Supports(kind string) bool {
	var cs []string
	_ = json.Unmarshal(a.Capabilities, &cs)
	for _, c := range cs {
		if c == kind {
			return true
		}
	}
	return false
}

type Artifact struct {
	ID              int64           `json:"id,string" gorm:"primaryKey"`
	TaskID          int64           `json:"task_id,string"`
	AttemptID       int64           `json:"attempt_id,string"`
	Kind            string          `json:"kind"`
	Name            string          `json:"name"`
	ObjectKey       string          `json:"-"`
	ContentType     string          `json:"content_type"`
	Size            int64           `json:"size"`
	SHA256          string          `json:"sha256" gorm:"column:sha256"`
	Status          string          `json:"status"`
	CreatedAt       time.Time       `json:"created_at"`
	Format          string          `json:"format" gorm:"default:folded"`
	Metadata        json.RawMessage `json:"metadata" gorm:"type:jsonb;default:'{}'"`
	ExpiresAt       *time.Time      `json:"expires_at"`
	RetentionClass  string          `json:"retention_class"`
	DownloadAllowed bool            `json:"download_allowed" gorm:"default:true"`
}
type Event struct {
	Sequence  int64           `json:"sequence,string" gorm:"primaryKey;autoIncrement"`
	TaskID    int64           `json:"task_id,string"`
	AttemptID *int64          `json:"attempt_id,omitempty,string"`
	Type      string          `json:"type"`
	Source    string          `json:"source"`
	Payload   json.RawMessage `json:"payload" gorm:"type:jsonb"`
	CreatedAt time.Time       `json:"created_at"`
}

func (Event) TableName() string { return "task_events" }

type Schedule struct {
	ID              int64           `json:"id,string" gorm:"primaryKey"`
	OwnerID         string          `json:"creator_id" gorm:"column:creator_id"`
	Name            string          `json:"name"`
	IntervalMinutes int             `json:"interval_minutes"`
	Cron            string          `json:"cron"`
	GroupID         *string         `json:"group_id"`
	Visibility      string          `json:"visibility" gorm:"default:private"`
	Timezone        string          `json:"timezone"`
	Template        json.RawMessage `json:"template" gorm:"type:jsonb"`
	Enabled         bool            `json:"enabled"`
	NextRunAt       time.Time       `json:"next_run_at"`
	LastRunAt       *time.Time      `json:"last_run_at,omitempty"`
	CreatedAt       time.Time       `json:"created_at"`
	UpdatedAt       time.Time       `json:"updated_at"`
}
type Audit struct {
	ID           int64           `json:"id,string" gorm:"primaryKey"`
	UserID       string          `json:"user_id"`
	Action       string          `json:"action"`
	ResourceType string          `json:"resource_type"`
	ResourceID   string          `json:"resource_id"`
	RequestID    string          `json:"request_id"`
	Detail       json.RawMessage `json:"detail" gorm:"type:jsonb"`
	CreatedAt    time.Time       `json:"created_at"`
}

func (Audit) TableName() string { return "audit_logs" }

type Outbox struct {
	ID          int64 `gorm:"primaryKey"`
	TaskID      int64
	AttemptID   int64
	EventType   string
	Payload     json.RawMessage `gorm:"type:jsonb"`
	Attempt     int
	AvailableAt time.Time
	PublishedAt *time.Time
	LastError   *string
	CreatedAt   time.Time
}

func (Outbox) TableName() string { return "outbox" }
func EventFor(t Task, attemptID *int64, kind string, payload any) Event {
	b, _ := json.Marshal(payload)
	return Event{TaskID: t.ID, AttemptID: attemptID, Type: kind, Source: "api", Payload: b, CreatedAt: time.Now().UTC()}
}
func RawKey(id int64, attempt int) string {
	return fmt.Sprintf("raw/tasks/%d/attempts/%d/profile.folded", id, attempt)
}
