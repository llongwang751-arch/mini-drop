// 两个独立 Python 进程使用真实 AgentScope SDK 与同一 PostgreSQL 账本，验证进程退出后的恢复。
// stdio 仅是此合成测试的传输夹具，生产仍使用双 UDS/SO_PEERCRED；供应商请求由 MockTransport 拦截，不接触真实模型。
package service

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"gorm.io/gorm"
)

const importedCheckpointProcess = `
import asyncio,os,sys
sys.path.insert(0,os.environ['DROP_CHECKPOINT_RUNTIME'])
import httpx2 as httpx
from drop_agent_runtime.config import Config
from drop_agent_runtime.runner import RunRequest,run_agent
from drop_agent_runtime.wire import json_bytes,strict_json,accepted_json_field,ProtocolError
body=strict_json(sys.stdin.buffer.readline())
mode=os.environ['DROP_CHECKPOINT_FIXTURE_MODE']
class StdioGateway:
 cancelled=False
 async def call(self,payload):
  sys.stdout.buffer.write(json_bytes({'callback':payload})+b'\n');sys.stdout.buffer.flush()
  raw=await asyncio.to_thread(sys.stdin.buffer.readline)
  if not raw:raise ProtocolError('AGENT_RUNTIME_GATEWAY_UNAVAILABLE')
  value=strict_json(raw)
  if value.get('accepted') is True or value.get('terminal') is True:value['result']=accepted_json_field(raw)
  return value
async def provider(request):
 if mode=='accepted':raise AssertionError('accepted checkpoint must not call provider')
 message={'role':'assistant','content':'{"summary":"explicit imported recovered report"}'}
 if mode=='first':
  message={'role':'assistant','content':None,'tool_calls':[{'id':i,'type':'function','function':{'name':n,'arguments':'{}'}} for i,n in [('process-hotspot','inspect_hotspots'),('process-quality','check_collection_quality')]]}
 return httpx.Response(200,json={'id':'imported-process-fixture','object':'chat.completion','created':1,'model':'deepseek-flash','choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if mode=='first' else 'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
async def main():
 config=Config('unused','unused',10001,'https://api.deepseek.com','deepseek-flash',os.environ['DROP_CHECKPOINT_TEST_KEY_FILE'])
 result=await run_agent(RunRequest.parse(body,config.model),config,StdioGateway(),transport=httpx.MockTransport(provider))
 sys.stdout.buffer.write(json_bytes({'result':result})+b'\n');sys.stdout.buffer.flush()
asyncio.run(main())
`

func TestPostgresAgentCheckpointAcrossIndependentSDKProcesses(t *testing.T) {
	db, store, run, owner := checkpointFixture(t)
	root, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	python := os.Getenv("AGENTSCOPE_TEST_PYTHON")
	if python == "" {
		python = filepath.Join(root, ".local", "agentscope-venv", "Scripts", "python.exe")
	}
	if _, err = os.Stat(python); err != nil {
		t.Skip("AGENTSCOPE_TEST_PYTHON with locked AgentScope2.0.10 is required for independent-process SDK fixture")
	}
	key := filepath.Join(t.TempDir(), "imported-test-key")
	if err = os.WriteFile(key, []byte("explicit-imported-only-key"), 0600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	var executions atomic.Int32
	definitions := []map[string]any{}
	for _, name := range []string{"inspect_hotspots", "check_collection_quality"} {
		definitions = append(definitions, map[string]any{"type": "function", "function": map[string]any{"name": name, "description": "Explicit imported checkpoint fixture", "parameters": map[string]any{"type": "object", "properties": map[string]any{}, "additionalProperties": false}}})
	}
	makeRunner := func(mode string) *AgentRunner {
		runner := &AgentRunner{sessions: map[string]*agentSession{}}
		runner.client = &http.Client{Transport: fixtureRoundTripper(func(request *http.Request) (*http.Response, error) {
			body, err := io.ReadAll(request.Body)
			if err != nil {
				return nil, err
			}
			var identity struct {
				RunID      string `json:"run_id"`
				Capability string `json:"capability"`
			}
			if json.Unmarshal(body, &identity) != nil {
				return nil, errors.New("invalid imported process body")
			}
			command := exec.CommandContext(ctx, python, "-X", "utf8", "-c", importedCheckpointProcess)
			command.Env = append(os.Environ(), "PYTHONUTF8=1", "DROP_CHECKPOINT_RUNTIME="+filepath.Join(root, "agent_runtime"), "DROP_CHECKPOINT_TEST_KEY_FILE="+key, "DROP_CHECKPOINT_FIXTURE_MODE="+mode)
			input, err := command.StdinPipe()
			if err != nil {
				return nil, err
			}
			output, err := command.StdoutPipe()
			if err != nil {
				return nil, err
			}
			var discarded bytes.Buffer
			command.Stderr = &discarded
			if err = command.Start(); err != nil {
				return nil, err
			}
			defer input.Close()
			if _, err = input.Write(append(body, '\n')); err != nil {
				command.Process.Kill()
				command.Wait()
				return nil, err
			}
			scanner := bufio.NewScanner(output)
			scanner.Buffer(make([]byte, 4096), 524288)
			for scanner.Scan() {
				var frame map[string]json.RawMessage
				if json.Unmarshal(scanner.Bytes(), &frame) != nil {
					command.Process.Kill()
					command.Wait()
					return nil, errors.New("invalid imported process frame")
				}
				if result, ok := frame["result"]; ok {
					if err = command.Wait(); err != nil {
						return nil, errors.New("imported SDK process failed")
					}
					return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(bytes.NewReader(result))}, nil
				}
				var call agentCallback
				if !decodeAgentJSON(frame["callback"], &call) {
					command.Process.Kill()
					command.Wait()
					return nil, errors.New("invalid imported callback")
				}
				callback := httptest.NewRequest(http.MethodPost, "/v1/callback/"+identity.RunID, bytes.NewReader(frame["callback"]))
				callback.Header.Set("Authorization", "Bearer "+identity.Capability)
				recorder := httptest.NewRecorder()
				runner.ServeHTTP(recorder, callback)
				if mode == "first" && call.Type == "tool" {
					command.Process.Kill()
					command.Wait()
					return nil, errors.New("imported process terminated after durable tool receipt")
				}
				if _, err = input.Write(append(recorder.Body.Bytes(), '\n')); err != nil {
					command.Process.Kill()
					command.Wait()
					return nil, err
				}
			}
			command.Process.Kill()
			command.Wait()
			return nil, errors.New("imported SDK process exited without result")
		})}
		return runner
	}
	// 这是传输层的已接受v2事实夹具，不代替真实事实来源/单位/权限验收；只验证每层都保留原数值词法。
	accepted := json.RawMessage(`{"report_version":2,"summary":"Go accepted imported process recovery ` + strings.Repeat("x", 24576) + `","facts":[{"id":"F1","value":0.000001},{"id":"F2","value":1e-6},{"id":"F3","value":-0},{"id":"F4","value":0.12345678901234567890123456789},{"id":"F5","value":90071992547409931234567890123456789}]}`)
	spec := agentRunSpec{Phase: "diagnosis", Model: "deepseek-flash", SystemPrompt: "Explicit imported cross-process fixture", Messages: []diagnosisMessage{{Role: "user", Content: "Explicit synthetic evidence"}}, Tools: definitions, Checkpoint: store, Fence: func(ctx context.Context) error {
		return db.WithContext(ctx).Transaction(func(tx *gorm.DB) error { return store.Fence(ctx, tx) })
	}, Invoke: func(context.Context, string, json.RawMessage) (agentToolResult, error) {
		executions.Add(1)
		return agentToolResult{Observation: json.RawMessage(`{"evidence_id":"E2","source":"explicit-imported-process-fixture"}`)}, nil
	}, Finish: func(context.Context, string) (json.RawMessage, error) {
		return accepted, nil
	}}
	if _, _, err = makeRunner("first").Run(ctx, spec); err == nil {
		t.Fatal("首次夹具未在回执后退出")
	}
	if executions.Load() != 1 {
		t.Fatal("退出前动作次数异常", executions.Load())
	}
	*owner = "new-process-worker"
	if err = db.Model(&DiagnosisRun{}).Where("id=?", run.ID).Update("lease_owner", *owner).Error; err != nil {
		t.Fatal(err)
	}
	spec.Checkpoint = NewAgentCheckpointStore(store.Repo, store.Binding, store.Fence)
	result, proof, err := makeRunner("resume").Run(ctx, spec)
	if err != nil || proof == nil {
		t.Fatal("新独立进程无法恢复", err)
	}
	if executions.Load() != 2 || proof.ModelCalls != 2 || proof.ToolCalls != 2 || proof.AgentClass != "agentscope.agent.Agent" || !bytes.Equal(result, accepted) {
		t.Fatal("恢复重放动作或重置计数", executions.Load(), proof)
	}
	// 第三个独立SDK进程只读取Go已验收的字符串快照；不能重复模型/工具或通过float64改写facts.value。
	*owner = "accepted-result-worker"
	if err = db.Model(&DiagnosisRun{}).Where("id=?", run.ID).Update("lease_owner", *owner).Error; err != nil {
		t.Fatal(err)
	}
	result, proof, err = makeRunner("accepted").Run(ctx, spec)
	if err != nil || proof == nil || proof.ModelCalls != 2 || proof.ToolCalls != 2 || executions.Load() != 2 || !bytes.Equal(result, accepted) {
		t.Fatal("已接受v2事实恢复改变数值词法或重新执行", err, proof)
	}
}
