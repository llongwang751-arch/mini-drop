// Package service：在派发前补充目录驱动的类型化采集参数、输入附件和有限上传清单。
// 采集 Agent 只拿本次 attempt 的授权，采集回执与后续 Analyzer 结果分开形成证据。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"path"

	pb "drop/api/gen/dropv1"
	"drop/api/internal/domain"
)

// enrichDispatch 按权威目录填充本次 TaskDesc 的制品、输入和采集参数。
// 所有对象授权都限定当前 task/attempt；日志与采集回执的固定清单不能扩大成任意路径上传能力。
func (s *Tasks) enrichDispatch(ctx context.Context, desc *pb.TaskDesc, p domain.Parameters, k domain.TaskKind, a domain.Attempt) error {
	desc.ProtocolVersion = 2
	desc.RawFormat = k.InputFormat
	desc.RawName = k.RawName
	desc.RawContentType = k.RawContentType
	desc.AnalysisPipeline = k.Pipeline
	desc.MaxFileBytes = k.MaxFileBytes
	if p.Budget != nil {
		b := p.Budget
		desc.Budget = &pb.ResourceBudget{MaxFileBytes: b.MaxFileBytes, MaxAddressSpaceBytes: b.MaxAddressSpaceBytes, MaxCpuSeconds: int32(b.MaxCPUSeconds), MaxProcesses: b.MaxProcesses, MaxConcurrency: b.MaxConcurrency}
		if b.MaxFileBytes > 0 {
			desc.MaxFileBytes = min(desc.MaxFileBytes, b.MaxFileBytes)
		}
	}
	target := p.Target
	if target == nil {
		target = &domain.Target{Type: p.Scope, PID: p.PID}
	}
	desc.Target = &pb.TargetDesc{Type: target.Type, Pid: int32(target.PID), StartTimeTicks: target.StartTimeTicks, ContainerId: target.ContainerID, Runtime: target.Runtime, ContainerPid: int32(target.ContainerPID), ExpectedUid: target.ExpectedUID, CheckUid: target.CheckUID}
	opts, _ := json.Marshal(p.Options)
	if string(opts) == "null" {
		opts = []byte(`{}`)
	}
	desc.ExtraParametersJson = string(opts)
	str := func(key string) string { v, _ := p.Options[key].(string); return v }
	flag := func(key string) bool { v, _ := p.Options[key].(bool); return v }
	num := func(key string) int64 { v, _ := p.Options[key].(float64); return int64(v) }
	switch k.ID {
	case "PERF_CPU":
		desc.Payload = &pb.TaskDesc_Perf{Perf: &pb.PerfTask{Event: str("event"), CallGraph: str("call_graph")}}
	case "JAVA_CPU":
		desc.Payload = &pb.TaskDesc_JavaProfiler{JavaProfiler: &pb.JavaProfilerTask{Event: str("event"), OutputFormat: "folded"}}
	case "GO_CPU", "GO_HEAP":
		desc.Payload = &pb.TaskDesc_GoProfile{GoProfile: &pb.GoProfileTask{Port: int32(num("port")), Path: str("path"), ProfileType: str("profile_type")}}
	case "PYTHON_CPU", "PYTHON_MEMORY":
		desc.Payload = &pb.TaskDesc_PythonProfiler{PythonProfiler: &pb.PythonProfilerTask{Mode: str("mode"), Native: flag("native")}}
	case "JAVA_HEAP":
		desc.Payload = &pb.TaskDesc_Heap{Heap: &pb.HeapTask{Format: "hprof", Live: flag("live")}}
	case "IO_TRACE", "MEMORY_TRACE":
		desc.Payload = &pb.TaskDesc_Trace{Trace: &pb.TraceTask{Category: str("category"), MinDurationUs: num("min_duration_us")}}
	case "GPERF_CPU", "GPERF_HEAP", "JEPROF_HEAP":
		desc.Payload = &pb.TaskDesc_Allocator{Allocator: &pb.AllocatorTask{Mode: str("mode")}}
	case "BOLT_PROFILE":
		desc.Payload = &pb.TaskDesc_Bolt{Bolt: &pb.BoltTask{BinaryPath: str("binary_path"), Optimize: flag("optimize")}}
	case "SCRIPT":
		desc.Payload = &pb.TaskDesc_Script{Script: &pb.ScriptTask{TemplateId: str("template_id")}}
	default:
		desc.Payload = &pb.TaskDesc_Native{Native: &pb.NativeTask{Category: k.ID}}
	}
	grants := []map[string]any{}
	auxiliary := append([]domain.AuxiliaryArtifact(nil), k.Auxiliary...)
	// 公开诊断日志仅包含白名单内的运维字段，不能装入采集器原始 stderr 或性能文件正文。
	// 采集 Agent 不持有 S3 长期密钥，只得到当前操作所需的临时授权。
	auxiliary = append(auxiliary, domain.AuxiliaryArtifact{Name: "runner.log", Kind: "LOG", Format: "log_jsonl", MaxFileBytes: 16384})
	// 采集回执独立于 Analyzer 输出，即使后续分析失败也应存在。
	// 它只接收当前 attempt 的 PUT 授权，不能发布到其他轮次。
	auxiliary = append(auxiliary, domain.AuxiliaryArtifact{Name: "collection-manifest.json", Kind: "MANIFEST", Format: "collection_manifest_v1", MaxFileBytes: 65536})
	directory := fmt.Sprintf("raw/tasks/%d/attempts/%d", a.TaskID, a.Attempt)
	if a.TaskID <= 0 || a.Attempt <= 0 || path.Dir(a.RawObjectKey) != directory {
		return domain.Fail("INVALID_STATE", "原始制品对象前缀无效")
	}
	for _, aux := range auxiliary {
		// 已排队的旧 attempt 可能保留上一版目录的 RAW 文件名。
		// 上传边界由 task/attempt 目录限定，而不是仅凭文件名判断归属。
		key := directory + "/" + aux.Name
		if aux.Kind == "MANIFEST" {
			key = fmt.Sprintf("manifest/tasks/%d/attempts/%d/%s", a.TaskID, a.Attempt, aux.Name)
		} else if aux.Kind == "BINARY" {
			key = fmt.Sprintf("binary/tasks/%d/attempts/%d/%s", a.TaskID, a.Attempt, aux.Name)
		}
		url, err := s.Storage.PresignPut(ctx, key)
		if err != nil {
			return err
		}
		desc.UploadTargets = append(desc.UploadTargets, &pb.UploadTarget{Name: aux.Name, ObjectKey: key, PutUrl: url, Kind: aux.Kind, MaxFileBytes: aux.MaxFileBytes})
		contentType := "application/octet-stream"
		if aux.Kind == "LOG" {
			contentType = "text/plain"
		} else if aux.Kind == "MANIFEST" {
			contentType = "application/json"
		}
		grants = append(grants, map[string]any{"name": aux.Name, "object_key": key, "put_url": url, "kind": aux.Kind, "max_file_bytes": aux.MaxFileBytes, "format": aux.Format, "content_type": contentType})
	}
	b, _ := json.Marshal(grants)
	return s.Repo.SetUploadTargets(ctx, a.ID, b)
}
