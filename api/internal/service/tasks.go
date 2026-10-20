// Package service：普通任务的编排层，连接 HTTP、领域校验、授权仓储、对象存储和 Core 控制面。
// 创建与重试先建立持久化身份，再通过 Outbox 投递；导入上传失败可按原幂等键恢复，不能伪造已采集状态。
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"time"

	"drop/api/internal/telemetry"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
	"gorm.io/gorm"

	pb "drop/api/gen/dropv1"
	"drop/api/internal/domain"
	"drop/api/internal/repository"
)

type ObjectStore interface {
	PutText(context.Context, string, string) (int64, string, error)
	PutBytes(context.Context, string, []byte, string) (int64, string, error)
	PresignPut(context.Context, string) (string, error)
	PresignGet(context.Context, string) (string, error)
	Ready(context.Context) error
}
type Control interface {
	Dispatch(context.Context, *pb.TaskDesc) error
	Cancel(context.Context, int64, int64, string) error
	Ready(context.Context) error
}

// Tasks 组合仓储、对象存储和 Core 控制面接口，负责普通任务的业务编排；具体执行与分析分别由 C++ Agent 和 Python Analyzer 完成。
type Tasks struct {
	Repo    *repository.Repository
	Storage ObjectStore
	Control Control
}

func Audit(u domain.User, action, kind, id, request string) domain.Audit {
	return domain.Audit{UserID: u.ID, Action: action, ResourceType: kind, ResourceID: id, RequestID: request, Detail: []byte("{}"), CreatedAt: time.Now().UTC()}
}
func ValidateKey(key string) *domain.Error {
	if len(key) < 8 || len(key) > 128 || strings.ContainsAny(key, "\r\n\x00") {
		return domain.Fail("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key 长度必须为 8–128 字符")
	}
	return nil
}

// Create 先完成角色、幂等键、类型目录、组和目标资源校验，再建立持久化 task/attempt 身份。
// 现场采集由 Outbox 异步派发；导入模式先上传原始字节再登记制品，失败可携原幂等键恢复，不能重复生成执行轮次。
func (s *Tasks) Create(ctx context.Context, u domain.User, in domain.CreateTask, key, request string) (domain.Task, bool, error) {
	if !u.CanWrite() {
		return domain.Task{}, false, domain.Fail("FORBIDDEN", "该角色只有只读权限")
	}
	if err := ValidateKey(key); err != nil {
		return domain.Task{}, false, err
	}
	in.Normalize()
	if in.Kind == "IMPORT_COLLAPSED" || in.Import != nil {
		in.Parameters.PID = 0
		in.Parameters.DurationSeconds = 0
		in.Parameters.FrequencyHz = 0
		in.Parameters.Scope = ""
		in.Parameters.Target = nil
	}
	if err := in.Validate(); err != nil {
		return domain.Task{}, false, err
	}
	if err := s.Repo.CheckGroup(ctx, u, in.GroupID, true); err != nil {
		return domain.Task{}, false, err
	}
	imported := in.Kind == "IMPORT_COLLAPSED" || in.Import != nil
	body, _ := json.Marshal(in)
	hash := repository.DigestBytes(body)
	existing, err := s.Repo.Existing(ctx, u.ID, key)
	if err == nil {
		if existing.RequestHash != hash {
			return existing, false, domain.Fail("IDEMPOTENCY_CONFLICT", "该幂等键已用于不同请求")
		}
		if !imported || existing.CollectionStatus != "CREATED" {
			return existing, true, nil
		}
	} else if !errors.Is(err, gorm.ErrRecordNotFound) {
		return domain.Task{}, false, err
	}
	if !imported {
		a, err := s.Repo.AuthorizedAgent(ctx, u, in.AgentID, true)
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return domain.Task{}, false, domain.Fail("AGENT_NOT_FOUND", "目标 Agent 不存在")
		}
		if err != nil {
			return domain.Task{}, false, err
		}
		if a.Status == "disabled" || a.Status == "offline" || time.Since(a.LastSeenAt) > 45*time.Second {
			return domain.Task{}, false, domain.Fail("AGENT_OFFLINE", "目标 Agent 当前离线")
		}
		if !a.Supports(in.Kind) {
			return domain.Task{}, false, domain.Fail("CAPABILITY_UNAVAILABLE", "目标 Agent 不具备所选采集能力")
		}
		if err := validateTargetResources(&in, a.Resources); err != nil {
			return domain.Task{}, false, err
		}
	}
	carrier := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, carrier)
	in.Parameters.Traceparent = carrier.Get("traceparent")
	params, _ := json.Marshal(in.Parameters)
	if imported {
		in.Parameters.Source = "import"
		params, _ = json.Marshal(in.Parameters)
	}
	now := time.Now().UTC()
	deadline := now.Add(10 * time.Minute)
	t := domain.Task{Name: in.Name, Kind: in.Kind, Priority: in.Priority, Parameters: params, OwnerID: u.ID, GroupID: in.GroupID, Visibility: in.Visibility, CollectionStatus: "CREATED", AnalysisStatus: "PENDING", IdempotencyKey: key, RequestHash: hash, RequestID: request, DeadlineAt: &deadline, CreatedAt: now, UpdatedAt: now}
	t.TraceParent = carrier.Get("traceparent")
	if !imported {
		t.AgentID = &in.AgentID
	}
	t, a, replayed, err := s.Repo.CreateTask(ctx, t, !imported, Audit(u, "task.create", "task", "", request))
	if err != nil {
		return t, replayed, err
	}
	if imported && t.CollectionStatus == "CREATED" {
		k, _ := domain.Kind(in.Kind)
		data := []byte(in.CollapsedText)
		name, ct := k.RawName, k.RawContentType
		format := k.InputFormat
		if in.Import != nil {
			data, _ = in.Import.Decode()
			name = in.Import.Filename
			if in.Import.Format != "" {
				format = in.Import.Format
			}
			if format != k.InputFormat {
				ct = "application/octet-stream"
			}
		}
		size, sha, err := s.Storage.PutBytes(ctx, a.RawObjectKey, data, ct)
		if err != nil {
			return t, replayed, domain.Fail("STORAGE_UNAVAILABLE", "原始数据暂未写入，请用相同幂等键重试")
		}
		metadata, _ := json.Marshal(map[string]any{"source": "import", "declared_format": format})
		artifact := domain.Artifact{TaskID: t.ID, AttemptID: a.ID, Kind: "RAW", Name: name, ObjectKey: a.RawObjectKey, ContentType: ct, Format: format, Metadata: metadata, Size: size, SHA256: sha, Status: "ready", CreatedAt: now}
		aux := []domain.Artifact{}
		if in.Import != nil {
			for _, file := range in.Import.Auxiliary {
				b, _ := (domain.ImportInput{Filename: file.Filename, DataBase64: file.DataBase64}).Decode()
				key := fmt.Sprintf("binary/tasks/%d/attempts/%d/%s", a.TaskID, a.Attempt, file.Filename)
				size, sha, err := s.Storage.PutBytes(ctx, key, b, "application/octet-stream")
				if err != nil {
					return t, replayed, domain.Fail("STORAGE_UNAVAILABLE", "附件尚未持久化，请用相同幂等键重试")
				}
				aux = append(aux, domain.Artifact{TaskID: t.ID, AttemptID: a.ID, Kind: "BINARY", Name: file.Filename, ObjectKey: key, ContentType: "application/octet-stream", Format: "elf", Metadata: []byte(`{"source":"import"}`), Size: size, SHA256: sha, Status: "ready", CreatedAt: now})
			}
		}
		if err := s.Repo.ImportWithAux(ctx, t, artifact, aux); err != nil {
			return t, replayed, err
		}
		t.CollectionStatus = "COLLECTED"
	}
	return t, replayed, nil
}

// Authorize 根据数据库中当前用户和资源关系核验读取权限，供普通接口与 SSE 循环复用，避免长连接持有过期角色。
func (s *Tasks) Authorize(ctx context.Context, u domain.User, id int64) (domain.Task, error) {
	t, err := s.Repo.Task(ctx, u, id)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return t, domain.Fail("NOT_FOUND", "任务不存在或无权访问")
	}
	if err != nil {
		return t, err
	}
	permissions, err := s.Repo.TaskPermissions(ctx, u, t)
	if err != nil {
		return t, err
	}
	t.Permissions = &permissions
	return t, nil
}
func (s *Tasks) Cancel(ctx context.Context, u domain.User, id int64, request string) (domain.Task, error) {
	if !u.CanWrite() {
		return domain.Task{}, domain.Fail("FORBIDDEN", "该角色只有只读权限")
	}
	if _, err := s.Repo.AuthorizeTaskWrite(ctx, u, id); err != nil {
		return domain.Task{}, err
	}
	return s.Repo.Cancel(ctx, u, id, Audit(u, "task.cancel", "task", strconv.FormatInt(id, 10), request))
}
func (s *Tasks) Retry(ctx context.Context, u domain.User, id int64, key, request string) (domain.Task, bool, error) {
	if !u.CanWrite() {
		return domain.Task{}, false, domain.Fail("FORBIDDEN", "该角色只有只读权限")
	}
	if err := ValidateKey(key); err != nil {
		return domain.Task{}, false, err
	}
	if _, err := s.Repo.AuthorizeTaskWrite(ctx, u, id); err != nil {
		return domain.Task{}, false, err
	}
	t, replay, err := s.Repo.Retry(ctx, u, id, key, request)
	if err == nil {
		_ = s.Repo.Audit(ctx, Audit(u, "task.retry", "task", strconv.FormatInt(id, 10), request))
		t, err = s.Repo.Task(ctx, u, id)
	}
	return t, replay, err
}
func (s *Tasks) Download(ctx context.Context, u domain.User, id, artifactID int64, request string) (string, error) {
	url, _, err := s.DownloadGrant(ctx, u, id, artifactID, request)
	return url, err
}

// DownloadGrant 重新核验任务和制品权限、归属及就绪状态，并生成带审计的短期下载授权。
// 过期时间属于本次请求，浏览器不能把一个 URL 当作永久资源权限。
func (s *Tasks) DownloadGrant(ctx context.Context, u domain.User, id, artifactID int64, request string) (string, time.Time, error) {
	if _, err := s.Authorize(ctx, u, id); err != nil {
		return "", time.Time{}, err
	}
	as, err := s.Repo.Artifacts(ctx, id)
	if err != nil {
		return "", time.Time{}, err
	}
	for _, a := range as {
		if a.ID == artifactID && a.Status == "ready" {
			if !a.DownloadAllowed || (a.ExpiresAt != nil && !a.ExpiresAt.After(time.Now())) {
				return "", time.Time{}, domain.Fail("ARTIFACT_EXPIRED", "制品已过期或禁止下载")
			}
			ttl := 5 * time.Minute
			if a.ExpiresAt != nil && time.Until(*a.ExpiresAt) < ttl {
				ttl = time.Until(*a.ExpiresAt)
			}
			if ttl < time.Second {
				return "", time.Time{}, domain.Fail("ARTIFACT_EXPIRED", "制品保留期即将结束")
			}
			expires := time.Now().UTC().Add(ttl)
			var url string
			var err error
			if store, ok := s.Storage.(interface {
				PresignGetFor(context.Context, string, time.Duration) (string, error)
			}); ok {
				url, err = store.PresignGetFor(ctx, a.ObjectKey, ttl)
			} else {
				if ttl < 5*time.Minute {
					return "", time.Time{}, domain.Fail("ARTIFACT_EXPIRED", "剩余保留期不足")
				}
				url, err = s.Storage.PresignGet(ctx, a.ObjectKey)
			}
			if err != nil {
				return "", time.Time{}, err
			}
			if err := s.Repo.Audit(ctx, Audit(u, "artifact.download", "artifact", strconv.FormatInt(a.ID, 10), request)); err != nil {
				return "", time.Time{}, err
			}
			return url, expires, nil
		}
	}
	return "", time.Time{}, domain.Fail("NOT_FOUND", "制品不存在或尚未就绪")
}

// Run 启动派发、定时计划、组合推进和清理等有界后台循环；共享 context 用于优雅退出，工作事实仍以数据库为准。
func (s *Tasks) Run(ctx context.Context) {
	go tick(ctx, time.Second, "COMPOSITE_ERROR", s.TickComposite)
	go tick(ctx, time.Second, "SCHEDULE_ERROR", s.schedule)
	go tick(ctx, time.Second, "IMPORT_RECOVERY_ERROR", s.Repo.SweepStaleImport)
	go tick(ctx, time.Minute, "EPHEMERAL_CLEANUP_ERROR", s.Repo.CleanupEphemeral)
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			for i := 0; i < 10; i++ {
				if err := s.dispatch(ctx); err != nil {
					slog.Error("outbox dispatch failed", "code", "OUTBOX_ERROR")
					break
				}
			}
		}
	}
}

// tick 用固定间隔驱动一次性工作函数，隔离单轮失败；循环只记录安全错误类别，退出由 context 统一控制。
func tick(ctx context.Context, interval time.Duration, code string, fn func(context.Context) error) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := fn(ctx); err != nil && ctx.Err() == nil {
				slog.Error("background operation failed", "code", code)
			}
		}
	}
}

// dispatch 根据 Outbox 中的任务和 attempt 构造类型化描述，补充临时对象授权后调用 Core。
// Core 可能已接受但 ACK 未达，最终状态由仓储的重读栅栏决定，不能只看这次 RPC 返回值。
func (s *Tasks) dispatch(ctx context.Context) error {
	return s.Repo.DispatchOne(ctx, func(o domain.Outbox, t domain.Task, a domain.Attempt) error {
		rpcCtx, cancel := context.WithTimeout(ctx, 4*time.Second)
		defer cancel()
		linked := otel.GetTextMapPropagator().Extract(ctx, propagation.MapCarrier{"traceparent": t.TraceParent})
		spanCtx := trace.SpanContextFromContext(linked)
		opts := []trace.SpanStartOption{}
		if spanCtx.IsValid() {
			opts = append(opts, trace.WithLinks(trace.Link{SpanContext: spanCtx}))
		}
		rpcCtx, span := otel.Tracer("drop/api/outbox").Start(rpcCtx, "task.dispatch", opts...)
		defer span.End()
		span.SetAttributes(telemetry.TaskAttributes(t.ID)...)
		if o.EventType == "CANCEL" {
			return s.Control.Cancel(rpcCtx, t.ID, o.AttemptID, t.RequestID)
		}
		url, err := s.Storage.PresignPut(rpcCtx, a.RawObjectKey)
		if err != nil {
			return err
		}
		if err := s.Repo.SetUploadURL(rpcCtx, a.ID, url); err != nil {
			return err
		}
		var p domain.Parameters
		if err := json.Unmarshal(t.Parameters, &p); err != nil {
			return err
		}
		agent := ""
		if t.AgentID != nil {
			agent = *t.AgentID
		}
		deadline := time.Now().Add(10 * time.Minute)
		if t.DeadlineAt != nil {
			deadline = *t.DeadlineAt
		}
		desc := &pb.TaskDesc{TaskId: t.ID, AttemptId: a.ID, Kind: t.Kind, Priority: int32(t.Priority), TargetAgentId: agent, Parameters: &pb.TaskParameters{Pid: int32(p.PID), DurationSeconds: int32(p.DurationSeconds), FrequencyHz: int32(p.FrequencyHz), Scope: p.Scope}, DeadlineUnixMs: deadline.UnixMilli(), RawUploadUrl: url, RawObjectKey: a.RawObjectKey, RequestId: t.RequestID, MaxFileBytes: 32 << 20}
		kind, _ := domain.Kind(t.Kind)
		desc.Traceparent = t.TraceParent
		if err := s.enrichDispatch(rpcCtx, desc, p, kind, a); err != nil {
			return err
		}
		return s.Control.Dispatch(rpcCtx, desc)
	})
}
func (s *Tasks) schedule(ctx context.Context) error {
	return s.Repo.TickSchedule(ctx, func(r *repository.Repository, sc domain.Schedule) (int64, error) {
		u, err := r.User(ctx, sc.OwnerID)
		if err != nil {
			return 0, err
		}
		var in domain.CreateTask
		if err := json.Unmarshal(sc.Template, &in); err != nil {
			return 0, domain.Fail("INVALID_TEMPLATE", "计划模板无效")
		}
		in.GroupID = sc.GroupID
		in.Visibility = sc.Visibility
		clone := *s
		clone.Repo = r
		t, _, err := clone.Create(ctx, u, in, fmt.Sprintf("schedule-%d-%d", sc.ID, sc.NextRunAt.Unix()), "scheduled")
		return t.ID, err
	})
}
