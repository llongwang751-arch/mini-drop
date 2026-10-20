package domain

import (
	"encoding/base64"
	"encoding/json"
	"path"
	"regexp"
	"strings"
	"time"
	_ "time/tzdata"
	"unicode"

	"github.com/robfig/cron/v3"
)

type Target struct {
	Type           string `json:"type"`
	PID            int    `json:"pid,omitempty"`
	StartTimeTicks uint64 `json:"start_time_ticks,omitempty"`
	ContainerID    string `json:"container_id,omitempty"`
	Runtime        string `json:"runtime,omitempty"`
	ContainerPID   int    `json:"container_pid,omitempty"`
	ExpectedUID    uint32 `json:"expected_uid,omitempty"`
	CheckUID       bool   `json:"check_uid,omitempty"`
}
type TaskPermissions struct {
	Read      bool `json:"read"`
	Operate   bool `json:"operate"`
	Share     bool `json:"share"`
	Delete    bool `json:"delete"`
	Reanalyze bool `json:"reanalyze"`
}
type Budget struct {
	MaxFileBytes         int64 `json:"max_file_bytes,omitempty"`
	MaxAddressSpaceBytes int64 `json:"max_address_space_bytes,omitempty"`
	MaxCPUSeconds        int64 `json:"max_cpu_seconds,omitempty"`
	MaxProcesses         int32 `json:"max_processes,omitempty"`
	MaxConcurrency       int32 `json:"max_concurrency,omitempty"`
}
type ImportInput struct {
	Filename    string       `json:"filename"`
	ContentType string       `json:"content_type"`
	DataBase64  string       `json:"data_base64"`
	Format      string       `json:"format,omitempty"`
	Auxiliary   []ImportFile `json:"auxiliary,omitempty"`
}
type ImportFile struct {
	Filename    string `json:"filename"`
	ContentType string `json:"content_type"`
	DataBase64  string `json:"data_base64"`
	Format      string `json:"format,omitempty"`
}

func (i ImportInput) Decode() ([]byte, *Error) {
	if strings.TrimSpace(i.Filename) == "" || i.Filename == "." || i.Filename == ".." || strings.ContainsFunc(i.Filename, unicode.IsControl) || len(i.Filename) > 120 || path.Base(i.Filename) != i.Filename || strings.ContainsAny(i.Filename, "\\\r\n\x00") {
		return nil, Fail("INVALID_PARAMETERS", "导入文件名无效")
	}
	if len(i.DataBase64) > base64.StdEncoding.EncodedLen(32<<20) {
		return nil, Fail("INVALID_PARAMETERS", "原始导入不得超过32MiB")
	}
	b, err := base64.StdEncoding.Strict().DecodeString(i.DataBase64)
	if err != nil || len(b) == 0 || len(b) > 32<<20 {
		return nil, Fail("INVALID_PARAMETERS", "导入须为有效base64且原文为1字节至32MiB")
	}
	return b, nil
}
func NextSchedule(expr, zone string, interval int, after time.Time) (time.Time, error) {
	loc, err := time.LoadLocation(zone)
	if err != nil {
		return time.Time{}, Fail("INVALID_PARAMETERS", "IANA时区无效")
	}
	if expr == "" {
		if interval < 1 || interval > 10080 {
			return time.Time{}, Fail("INVALID_PARAMETERS", "间隔须为1至10080分钟")
		}
		return after.Add(time.Duration(interval) * time.Minute).UTC(), nil
	}
	if len(strings.Fields(expr)) != 5 || strings.ContainsAny(expr, "@=\r\n") {
		return time.Time{}, Fail("INVALID_PARAMETERS", "Cron必须为标准五段表达式")
	}
	spec, err := cron.NewParser(cron.Minute | cron.Hour | cron.Dom | cron.Month | cron.Dow).Parse("CRON_TZ=" + loc.String() + " " + expr)
	if err != nil {
		return time.Time{}, Fail("INVALID_PARAMETERS", "Cron表达式无效")
	}
	next := spec.Next(after)
	if next.IsZero() {
		return next, Fail("INVALID_PARAMETERS", "Cron在未来五年内无触发时点")
	}
	return next.UTC(), nil
}

type Group struct {
	ID          string    `json:"id" gorm:"primaryKey"`
	Name        string    `json:"name"`
	Description string    `json:"description"`
	CreatedAt   time.Time `json:"created_at"`
}
type CompositeNodeInput struct {
	Key       string     `json:"key"`
	Required  bool       `json:"required"`
	DependsOn []string   `json:"depends_on"`
	Task      CreateTask `json:"task"`
}
type CreateComposite struct {
	Name                string               `json:"name"`
	GroupID             *string              `json:"group_id,omitempty"`
	Visibility          string               `json:"visibility,omitempty"`
	DeadlineSeconds     int                  `json:"deadline_seconds"`
	AllowPartialSuccess bool                 `json:"allow_partial_success"`
	Nodes               []CompositeNodeInput `json:"nodes"`
}

func (c CreateComposite) Validate() *Error {
	if c.Name == "" || len(c.Name) > 120 || c.DeadlineSeconds < 1 || c.DeadlineSeconds > 3600 || len(c.Nodes) < 1 || len(c.Nodes) > 16 {
		return Fail("INVALID_PARAMETERS", "复合任务名称、期限或节点数无效")
	}
	nodes := map[string]CompositeNodeInput{}
	for _, n := range c.Nodes {
		if !regexp.MustCompile(`^[a-zA-Z][a-zA-Z0-9_-]{0,47}$`).MatchString(n.Key) {
			return Fail("INVALID_DAG", "节点key无效")
		}
		if _, ok := nodes[n.Key]; ok {
			return Fail("INVALID_DAG", "节点key重复")
		}
		if n.Task.Kind == "COMPOSITE" || n.Task.Import != nil || n.Task.CollapsedText != "" || n.Task.Kind == "IMPORT_COLLAPSED" {
			return Fail("INVALID_DAG", "复合任务只允许现场采集子任务")
		}
		nodes[n.Key] = n
	}
	state := map[string]int{}
	var visit func(string) bool
	visit = func(key string) bool {
		if state[key] == 1 {
			return false
		}
		if state[key] == 2 {
			return true
		}
		n, ok := nodes[key]
		if !ok {
			return false
		}
		state[key] = 1
		for _, d := range n.DependsOn {
			if !visit(d) {
				return false
			}
		}
		state[key] = 2
		return true
	}
	for key := range nodes {
		if !visit(key) {
			return Fail("INVALID_DAG", "依赖缺失或存在环")
		}
	}
	return nil
}

type CompositeRun struct {
	ID                  int64           `json:"id,string"`
	Name                string          `json:"name"`
	OwnerID             string          `json:"creator_id" gorm:"column:creator_id"`
	GroupID             *string         `json:"group_id"`
	Visibility          string          `json:"visibility"`
	Status              string          `json:"status"`
	AllowPartialSuccess bool            `json:"allow_partial_success"`
	DeadlineAt          time.Time       `json:"deadline_at"`
	Definition          json.RawMessage `json:"-" gorm:"type:jsonb"`
	Summary             json.RawMessage `json:"summary" gorm:"type:jsonb"`
	IdempotencyKey      string          `json:"-"`
	RequestHash         string          `json:"-"`
	CreatedAt           time.Time       `json:"created_at"`
	UpdatedAt           time.Time       `json:"updated_at"`
}
type CompositeNode struct {
	ID        int64           `json:"id,string"`
	RunID     int64           `json:"run_id,string"`
	Key       string          `json:"key"`
	Required  bool            `json:"required"`
	DependsOn json.RawMessage `json:"depends_on" gorm:"type:jsonb"`
	Template  json.RawMessage `json:"-" gorm:"type:jsonb"`
	TaskID    *int64          `json:"task_id,string,omitempty"`
	Status    string          `json:"status"`
	ErrorCode *string         `json:"error_code,omitempty"`
	CreatedAt time.Time       `json:"created_at"`
	UpdatedAt time.Time       `json:"updated_at"`
	Task      *Task           `json:"task,omitempty" gorm:"-"`
	Result    json.RawMessage `json:"result,omitempty" gorm:"-"`
}

func AggregateComposite(nodes []CompositeNode, partial bool) (string, map[string]int) {
	summary := map[string]int{"total": len(nodes), "waiting": 0, "running": 0, "succeeded": 0, "failed": 0, "skipped": 0}
	requiredFailed := false
	for _, n := range nodes {
		switch n.Status {
		case "WAITING":
			summary["waiting"]++
		case "RUNNING":
			summary["running"]++
		case "SUCCESS":
			summary["succeeded"]++
		case "FAILED":
			summary["failed"]++
			requiredFailed = requiredFailed || n.Required
		case "SKIPPED":
			summary["skipped"]++
			requiredFailed = requiredFailed || n.Required
		}
	}
	if summary["waiting"]+summary["running"] > 0 {
		return "RUNNING", summary
	}
	if requiredFailed || (!partial && summary["failed"]+summary["skipped"] > 0) || summary["succeeded"] == 0 {
		return "FAILED", summary
	}
	if summary["failed"]+summary["skipped"] > 0 {
		return "PARTIAL_SUCCESS", summary
	}
	return "SUCCESS", summary
}
