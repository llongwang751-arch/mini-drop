// Package service：把浏览器提交的目标与 Agent 最近上报的资源策略对照。
// 用户能看到进程不代表可随意指定 PID、端口或能力；规范目标身份要在入库前归一化并验证。
package service

import (
	"drop/api/internal/domain"
	"encoding/json"
	"strings"
)

// validateTargetResources 把请求目标与 Agent 上报的规范资源清单、语言和能力策略对照，不能接受客户端凭空指定的进程、端口或其他目标。
func validateTargetResources(in *domain.CreateTask, raw json.RawMessage) *domain.Error {
	var resource struct {
		Processes []struct {
			PID   int     `json:"pid"`
			UID   *uint32 `json:"uid"`
			Start uint64  `json:"start_time_ticks"`
		} `json:"processes"`
		Policy struct {
			HostProcesses    *bool    `json:"host_processes"`
			Containers       *bool    `json:"containers"`
			HostResources    *bool    `json:"host_resources"`
			LegacyHost       *bool    `json:"allow_host_resources"`
			LegacyContainers *bool    `json:"allow_containers"`
			AllowedIDs       []string `json:"allowed_container_ids"`
			AllowedUIDs      []uint32 `json:"allowed_uids"`
		} `json:"target_policy"`
	}
	deny := func() *domain.Error {
		return domain.Fail("TARGET_NOT_ALLOWED", "Agent策略未授权此目标；请检查PID身份、UID或容器精确白名单")
	}
	if json.Unmarshal(raw, &resource) != nil {
		return domain.Fail("CAPABILITY_UNAVAILABLE", "Agent资源快照格式无效")
	}
	enabled := func(current, legacy *bool) bool {
		if current != nil {
			return *current
		}
		return legacy != nil && *legacy
	}
	switch in.Parameters.Scope {
	case "host":
		if !enabled(resource.Policy.HostResources, resource.Policy.LegacyHost) {
			return deny()
		}
		return nil
	case "container":
		if !enabled(resource.Policy.Containers, resource.Policy.LegacyContainers) || in.Parameters.Target == nil {
			return deny()
		}
		for _, id := range resource.Policy.AllowedIDs {
			if strings.EqualFold(id, in.Parameters.Target.ContainerID) {
				in.Parameters.Target.ContainerID = strings.ToLower(id)
				return nil
			}
		}
		return deny()
	case "process":
		for _, p := range resource.Processes {
			if p.PID != in.Parameters.PID {
				continue
			}
			target := in.Parameters.Target
			if target == nil {
				target = &domain.Target{Type: "process", PID: p.PID}
				in.Parameters.Target = target
			}
			if target.StartTimeTicks != 0 && p.Start != 0 && target.StartTimeTicks != p.Start {
				return domain.Fail("TARGET_NOT_ALLOWED", "目标进程已变化，请刷新进程列表")
			}
			if target.CheckUID && p.UID != nil && target.ExpectedUID != *p.UID {
				return deny()
			}
			if target.StartTimeTicks == 0 {
				target.StartTimeTicks = p.Start
			}
			if p.UID != nil {
				target.ExpectedUID = *p.UID
				target.CheckUID = true
			}
			return nil
		}
		if resource.Policy.HostProcesses == nil || !*resource.Policy.HostProcesses {
			return deny()
		}
		target := in.Parameters.Target
		if target == nil {
			target = &domain.Target{Type: "process", PID: in.Parameters.PID}
			in.Parameters.Target = target
		}
		if !target.CheckUID && len(resource.Policy.AllowedUIDs) == 1 {
			target.ExpectedUID = resource.Policy.AllowedUIDs[0]
			target.CheckUID = true
		}
		if target.CheckUID {
			for _, uid := range resource.Policy.AllowedUIDs {
				if uid == target.ExpectedUID {
					return nil
				}
			}
		}
		return deny()
	default:
		return deny()
	}
}
