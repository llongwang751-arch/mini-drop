package service

import (
	"context"
	"encoding/json"
	"fmt"
	"path"

	pb "drop/api/gen/dropv1"
	"drop/api/internal/domain"
)

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
	// This fixed public diagnostic log contains allowlisted operational fields,
	// never the profiler's raw stderr or profile body. The Agent owns no S3 key.
	auxiliary = append(auxiliary, domain.AuxiliaryArtifact{Name: "runner.log", Kind: "LOG", Format: "log_jsonl", MaxFileBytes: 16384})
	// The collection receipt is independent of Analyzer output and exists even
	// if later analysis fails. It receives only this current attempt's PUT grant.
	auxiliary = append(auxiliary, domain.AuxiliaryArtifact{Name: "collection-manifest.json", Kind: "MANIFEST", Format: "collection_manifest_v1", MaxFileBytes: 65536})
	directory := fmt.Sprintf("raw/tasks/%d/attempts/%d", a.TaskID, a.Attempt)
	if a.TaskID <= 0 || a.Attempt <= 0 || path.Dir(a.RawObjectKey) != directory {
		return domain.Fail("INVALID_STATE", "原始制品对象前缀无效")
	}
	for _, aux := range auxiliary {
		// Existing queued attempts may retain the prior catalog's RAW basename.
		// The task/attempt directory, rather than that basename, scopes uploads.
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
