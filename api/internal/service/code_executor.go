// Package service：API 与独立源码执行器之间的类型化私有客户端。
// 动作只能针对已登记项目和版本；执行凭据与隔离环境由执行器管理，API 不持有任意宿主执行能力。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"path/filepath"
	"strings"
	"time"

	"drop/api/internal/domain"
)

// CodeExecutor 只允许 API 通过此 Unix socket 请求已登记项目的类型化动作。
// API 不接收 Docker socket、宿主源码根目录或任意 exec 接口，防止模型扩大执行权限。
type CodeExecutor struct{ client *http.Client }

func NewCodeExecutor(socket string) (*CodeExecutor, error) {
	if !filepath.IsAbs(socket) || strings.ContainsAny(socket, "\x00\r\n") || len(socket) > 200 {
		return nil, domain.Fail("INVALID_PARAMETERS", "代码执行器socket路径无效")
	}
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "unix", socket)
	}, MaxIdleConns: 2, MaxConnsPerHost: 2, ResponseHeaderTimeout: 245 * time.Second}
	return &CodeExecutor{client: &http.Client{Transport: transport, Timeout: 250 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}
func (c *CodeExecutor) Invoke(ctx context.Context, action, project, revision string, args any) (json.RawMessage, error) {
	return c.invoke(ctx, action, project, revision, "", args)
}

// InvokeRepair 要求每个新的修复运行重新测量；同一修复步骤重试时可复用它自己的持久化回执。
// 模型参数不能选择其他修复运行的作用域，也不能用历史成绩冒充本次测量。
func (c *CodeExecutor) InvokeRepair(ctx context.Context, runID int64, action, project, revision string, args any) (json.RawMessage, error) {
	if runID <= 0 {
		return nil, domain.Fail("INVALID_PARAMETERS", "源码修复执行标识无效")
	}
	return c.invoke(ctx, action, project, revision, fmt.Sprintf("repair:%d", runID), args)
}

// invoke 向登记项目执行器发送类型化动作及有界参数，限制响应大小并传播取消。
// 上游失败只映射为安全枚举，不把宿主路径、源码片段或执行器错误正文写入平台日志。
func (c *CodeExecutor) invoke(ctx context.Context, action, project, revision, executionID string, args any) (json.RawMessage, error) {
	if c == nil {
		return nil, domain.Fail("CODE_EXECUTOR_UNAVAILABLE", "未配置隔离代码执行器")
	}
	body, _ := json.Marshal(map[string]any{"action": action, "project_id": project, "source_revision": revision, "execution_id": executionID, "arguments": args})
	if len(body) > 49152 {
		return nil, domain.Fail("AI_TOOL_REJECTED", "代码工具输入超限")
	}
	req, err := http.NewRequestWithContext(ctx, "POST", "http://code-executor/rpc", strings.NewReader(string(body)))
	if err != nil {
		return nil, domain.Fail("CODE_EXECUTOR_UNAVAILABLE", "执行器请求无效")
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, domain.Fail("CODE_EXECUTOR_UNAVAILABLE", "隔离执行器请求失败")
	}
	raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 131073))
	resp.Body.Close()
	if readErr != nil || len(raw) > 131072 {
		return nil, domain.Fail("CODE_EXECUTOR_UNAVAILABLE", "隔离执行器响应超限")
	}
	var answer struct {
		Data      json.RawMessage `json:"data"`
		ErrorCode string          `json:"error_code"`
	}
	if json.Unmarshal(raw, &answer) != nil {
		return nil, domain.Fail("CODE_EXECUTOR_UNAVAILABLE", "隔离执行器响应无效")
	}
	if resp.StatusCode != 200 {
		// 仅向上层返回固定枚举形式的执行器错误码，不能回传原始上游错误正文。
		if len(answer.ErrorCode) < 1 || len(answer.ErrorCode) > 80 || strings.IndexFunc(answer.ErrorCode, func(r rune) bool { return !(r >= 'A' && r <= 'Z' || r == '_' || r >= '0' && r <= '9') }) >= 0 {
			return nil, domain.Fail("CODE_EXECUTOR_UNAVAILABLE", "隔离执行器拒绝请求")
		}
		return nil, domain.Fail(answer.ErrorCode, "隔离工具校验拒绝，请核对项目版本和工具参数")
	}
	return answer.Data, nil
}
