package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

func (s *Tasks) CreateComposite(ctx context.Context, u domain.User, in domain.CreateComposite, key, request string) (domain.CompositeRun, bool, error) {
	var run domain.CompositeRun
	if !u.CanWrite() {
		return run, false, domain.Fail("FORBIDDEN", "只读角色")
	}
	if err := ValidateKey(key); err != nil {
		return run, false, err
	}
	if in.Visibility == "" {
		in.Visibility = "private"
	}
	if in.Visibility != "private" && in.Visibility != "group" || in.Visibility == "group" && in.GroupID == nil {
		return run, false, domain.Fail("INVALID_PARAMETERS", "可见性无效")
	}
	if err := in.Validate(); err != nil {
		return run, false, err
	}
	if err := s.Repo.CheckGroup(ctx, u, in.GroupID, true); err != nil {
		return run, false, err
	}
	for i := range in.Nodes {
		in.Nodes[i].Task.GroupID = in.GroupID
		in.Nodes[i].Task.Visibility = in.Visibility
		in.Nodes[i].Task.Normalize()
		if err := in.Nodes[i].Task.Validate(); err != nil {
			return run, false, err
		}
	}
	b, _ := json.Marshal(in)
	hash := repository.Digest(string(b))
	replay := false
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,0))", "composite:"+u.ID+":"+key).Error; err != nil {
			return err
		}
		err := tx.Where("creator_id=? AND idempotency_key=?", u.ID, key).First(&run).Error
		if err == nil {
			if run.RequestHash != hash {
				return domain.Fail("IDEMPOTENCY_CONFLICT", "幂等键已用于其他复合任务")
			}
			replay = true
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		now := time.Now().UTC()
		run = domain.CompositeRun{Name: in.Name, OwnerID: u.ID, GroupID: in.GroupID, Visibility: in.Visibility, Status: "PENDING", AllowPartialSuccess: in.AllowPartialSuccess, DeadlineAt: now.Add(time.Duration(in.DeadlineSeconds) * time.Second), Definition: b, Summary: []byte(`{}`), IdempotencyKey: key, RequestHash: hash, CreatedAt: now, UpdatedAt: now}
		if err := tx.Create(&run).Error; err != nil {
			return err
		}
		for _, n := range in.Nodes {
			deps, _ := json.Marshal(n.DependsOn)
			if string(deps) == "null" {
				deps = []byte(`[]`)
			}
			template, _ := json.Marshal(n.Task)
			node := domain.CompositeNode{RunID: run.ID, Key: n.Key, Required: n.Required, DependsOn: deps, Template: template, Status: "WAITING", CreatedAt: now, UpdatedAt: now}
			if err := tx.Create(&node).Error; err != nil {
				return err
			}
		}
		return tx.Create(ptrAudit(Audit(u, "composite.create", "composite", fmt.Sprint(run.ID), request))).Error
	})
	return run, replay, err
}
func ptrAudit(a domain.Audit) *domain.Audit { return &a }
func (s *Tasks) Composites(ctx context.Context, u domain.User) ([]domain.CompositeRun, error) {
	runs := []domain.CompositeRun{}
	err := repository.ResourceScope(s.Repo.Context(ctx), u, "composite_runs", false).Order("created_at DESC,id DESC").Limit(100).Find(&runs).Error
	return runs, err
}
func (s *Tasks) Composite(ctx context.Context, u domain.User, id int64) (domain.CompositeRun, []domain.CompositeNode, error) {
	var run domain.CompositeRun
	nodes := []domain.CompositeNode{}
	if err := repository.ResourceScope(s.Repo.Context(ctx), u, "composite_runs", false).Where("id=?", id).First(&run).Error; err != nil {
		return run, nodes, err
	}
	if err := s.Repo.Context(ctx).Where("run_id=?", id).Order("id").Find(&nodes).Error; err != nil {
		return run, nodes, err
	}
	for i := range nodes {
		if nodes[i].TaskID != nil {
			t, err := s.Repo.Task(ctx, u, *nodes[i].TaskID)
			if err == nil {
				nodes[i].Task = &t
				nodes[i].Result, _ = s.Repo.ResultSummary(ctx, t.ID)
			}
		}
	}
	return run, nodes, nil
}
func (s *Tasks) CancelComposite(ctx context.Context, u domain.User, id int64, request string) error {
	if !u.CanWrite() {
		return domain.Fail("FORBIDDEN", "只读角色")
	}
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var run domain.CompositeRun
		if err := repository.ResourceScope(tx, u, "composite_runs", true).Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=?", id).First(&run).Error; err != nil {
			return err
		}
		if run.Status != "PENDING" && run.Status != "RUNNING" {
			return domain.Fail("INVALID_STATE", "复合任务已结束")
		}
		clone := *s
		clone.Repo = repository.New(tx)
		if err := clone.stopComposite(ctx, run, "CANCELED"); err != nil {
			return err
		}
		return tx.Create(ptrAudit(Audit(u, "composite.cancel", "composite", fmt.Sprint(id), request))).Error
	})
}
func (s *Tasks) stopComposite(ctx context.Context, run domain.CompositeRun, status string) error {
	var nodes []domain.CompositeNode
	if err := s.Repo.Context(ctx).Where("run_id=?", run.ID).Find(&nodes).Error; err != nil {
		return err
	}
	owner, err := s.Repo.User(ctx, run.OwnerID)
	if err != nil {
		return err
	}
	for _, n := range nodes {
		if n.TaskID != nil {
			t, err := s.Repo.Task(ctx, owner, *n.TaskID)
			if err != nil {
				return err
			}
			if !t.Terminal() {
				if _, err := s.Repo.Cancel(ctx, owner, t.ID, Audit(owner, "task.cancel", "task", fmt.Sprint(t.ID), "composite")); err != nil {
					return err
				}
			}
		}
		if n.Status == "WAITING" || n.Status == "RUNNING" {
			if err := s.Repo.Context(ctx).Model(&n).Updates(map[string]any{"status": "SKIPPED", "error_code": status, "updated_at": time.Now().UTC()}).Error; err != nil {
				return err
			}
		}
	}
	var updated []domain.CompositeNode
	if err := s.Repo.Context(ctx).Where("run_id=?", run.ID).Find(&updated).Error; err != nil {
		return err
	}
	_, summary := domain.AggregateComposite(updated, run.AllowPartialSuccess)
	b, _ := json.Marshal(summary)
	return s.Repo.Context(ctx).Model(&run).Updates(map[string]any{"status": status, "summary": string(b), "updated_at": time.Now().UTC()}).Error
}
func (s *Tasks) TickComposite(ctx context.Context) error {
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var run domain.CompositeRun
		err := tx.Clauses(clause.Locking{Strength: "UPDATE", Options: "SKIP LOCKED"}).Where("status IN ('PENDING','RUNNING')").Order("updated_at,id").First(&run).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil
		}
		if err != nil {
			return err
		}
		clone := *s
		clone.Repo = repository.New(tx)
		if !run.DeadlineAt.After(time.Now()) {
			return clone.stopComposite(ctx, run, "TIMED_OUT")
		}
		var nodes []domain.CompositeNode
		if err := tx.Where("run_id=?", run.ID).Order("id").Find(&nodes).Error; err != nil {
			return err
		}
		states := map[string]string{}
		for i := range nodes {
			n := &nodes[i]
			if n.Status == "RUNNING" && n.TaskID != nil {
				var t domain.Task
				if err := tx.Where("id=?", *n.TaskID).First(&t).Error; err != nil {
					return err
				}
				if t.Terminal() {
					n.Status = "FAILED"
					if t.AnalysisStatus == "SUCCESS" {
						n.Status = "SUCCESS"
					}
					n.ErrorCode = t.ErrorCode
					if err := tx.Model(n).Updates(map[string]any{"status": n.Status, "error_code": n.ErrorCode, "updated_at": time.Now().UTC()}).Error; err != nil {
						return err
					}
				}
			}
			states[n.Key] = n.Status
		}
		owner, err := clone.Repo.User(ctx, run.OwnerID)
		if err != nil {
			return err
		}
		for i := range nodes {
			n := &nodes[i]
			if n.Status != "WAITING" {
				continue
			}
			deps := []string{}
			if err := json.Unmarshal(n.DependsOn, &deps); err != nil {
				return err
			}
			ready, blocked := true, false
			for _, d := range deps {
				if states[d] != "SUCCESS" {
					ready = false
				}
				if states[d] == "FAILED" || states[d] == "SKIPPED" {
					blocked = true
				}
			}
			changes := map[string]any{"updated_at": time.Now().UTC()}
			if blocked {
				n.Status = "SKIPPED"
				changes["status"] = n.Status
				changes["error_code"] = "DEPENDENCY_FAILED"
			} else if ready {
				var in domain.CreateTask
				if err := json.Unmarshal(n.Template, &in); err != nil {
					return err
				}
				task, _, err := clone.Create(ctx, owner, in, fmt.Sprintf("composite-%d-%s", run.ID, n.Key), "composite")
				if err != nil {
					var de *domain.Error
					if !errors.As(err, &de) {
						return err
					}
					if de.Code == "QUOTA_EXCEEDED" {
						continue
					}
					n.Status = "FAILED"
					changes["error_code"] = de.Code
				} else {
					n.Status = "RUNNING"
					n.TaskID = &task.ID
					changes["task_id"] = task.ID
					if task.DeadlineAt == nil || task.DeadlineAt.After(run.DeadlineAt) {
						if err := tx.Model(&task).Update("collection_deadline", run.DeadlineAt).Error; err != nil {
							return err
						}
					}
				}
				changes["status"] = n.Status
			} else {
				continue
			}
			states[n.Key] = n.Status
			if err := tx.Model(n).Updates(changes).Error; err != nil {
				return err
			}
		}
		status, summary := domain.AggregateComposite(nodes, run.AllowPartialSuccess)
		b, _ := json.Marshal(summary)
		return tx.Model(&run).Updates(map[string]any{"status": status, "summary": string(b), "updated_at": time.Now().UTC()}).Error
	})
}
