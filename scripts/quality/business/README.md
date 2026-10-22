# 真实 Python CRM 的业务接口质量验证

这套工具补充 Drop 的业务测试能力：读取用户明确指定的 Python CRM 源码，执行联系人资源的接口、权限、租户隔离、输入边界和状态序列测试。模型可参与选择测试计划；需求断言和身份规则由工具固定。默认路径不调用模型，报告明确标记 `model_called_by_runner=false`。

## 接入的是哪部分

接入真实 CRM 的以下链路，不使用配色函数代替 HTTP 业务：

```text
FastAPI /api/v1/contacts 路由
  → 实际 Pydantic 输入 schema
  → 实际 get_identity / require_role 依赖
  → 实际 Contacts / Domain 业务服务
  → 实际 MemoryRepository、审计、事件
```

ASGITransport 通过 HTTP 请求模型执行真实路由，身份解析器、组织和联系人是明确的合成夹具。没有 TCP/TLS、GoTrue、PostgreSQL、RLS 或真实渠道，也不覆盖全部 CRM 接口。结果不能写成“全 CRM E2E 测试通过”或“发现三个真实漏洞”。

## 本机执行

使用 CRM 已安装依赖的 Python 环境，至少需要 Python 3.12、FastAPI、Pydantic 2 和 HTTPX。其余依赖取决于被测版本实际路由的传递导入；缺少依赖会生成失败报告，工具不会自动安装包或降低为模拟路由。推荐使用该 CRM 的锁文件准备独立环境。

```powershell
python -m unittest discover -s scripts/quality/business/tests -v
python scripts/quality/business/run.py --crm-source E:/crm/AiNativeCrm-python --output .local/quality-business/run-001
```

`--crm-source` 必须显式指定。输出必须是新目录，且与 CRM 目录互不包含。CRM 源码只读导入，Python 字节码写入关闭；运行前后散列全部 `crm/**/*.py`，不复制业务源码、不读取 `.env`、不使用真实业务数据。每次结果保留在独立目录；原失败不能被后一次成功覆盖。

当前固定目录有 47 个需求场景，覆盖：

- 接待成员创建、读取和修改；姓名及标签规范化。
- 姓名、手机号、标签、自定义字段名、日期、分页和搜索上下界。
- 匿名访问、viewer 禁止写入、请求体指定租户或计算字段的拒绝。
- 跨组织读取/修改/列表及游标组织绑定。
- JSON 格式、对象类型、UUID、未知字段和不支持的 HTTP 方法。
- 重复手机号、空修改、匿名化不可逆、创建→读取→修改→读取和审计/事件一致性。

这是有限场景内的测试计划与状态序列探索，不是任意业务自主探索。新增领域需要先审阅需求并扩展固定 oracle。

## 模型只选择结构化计划

导出不含 CRM 源码和凭据的上下文与输出 schema：

```powershell
python scripts/quality/business/run.py --emit-planner-context .local/quality-business/planner-context.json --emit-plan-schema .local/quality-business/plan.schema.json
```

模型上下文只有场景 ID 与分类，不发送具体预期状态、固定断言或实现源码。可使用现有 AgentScope/DeepSeek 调用器，或本目录的可选官方 SDK 入口生成 JSON：

```powershell
.local/agentscope-venv/Scripts/python.exe scripts/quality/business/plan_with_agentscope.py --key-file <本机受保护的密钥文件路径> --model deepseek-flash --output .local/quality-business/model-plan-001
```

该入口要求官方 AgentScope 2.0.10，使用原生 `Agent`、`DeepSeekChatModel`、唯一 `select_test_cases` 工具和预算 middleware。固定连接 `https://api.deepseek.com`，严格 TLS、不跟随重定向、不使用代理环境；最多 3 次模型调用、1 次工具调用、60 秒，不重试，temperature=0、max_tokens=1500。密钥仅从文件有界读取，进程清除业务/模型/代理环境，控制台及回执均不包含密钥或供应商原始异常。

输出 `plan.json` 和 `model-receipt.json`；回执记录实际模型、供应商 HTTP、工具次数、供应商返回的 token 用量及 `cost=null`。未返回用量时保留 `null`。计划经固定校验器接受后即终止，支持唯一工具提交或严格纯 JSON；不调用性能采集规划工具，也不授予任意 HTTP/文件能力。

示例计划：

```json
{
  "schema_version": 1,
  "case_ids": ["name_maximum", "name_too_long", "create_patch_read", "cursor_tampered"]
}
```

```powershell
python scripts/quality/business/run.py --crm-source E:/crm/AiNativeCrm-python --output .local/quality-business/model-execution-001 --plan .local/quality-business/model-plan-001/plan.json
```

只能选择已审阅的场景 ID；未知字段、URL、代码、预期结果、身份、重复 ID 和超长计划均被拒绝。固定的认证、权限、租户、重复号码和状态序列回归不可被模型移除。执行器不持有模型密钥、不调用模型，也不会仅凭一个 JSON 宣称已经使用 AI；真实调用需要独立、脱敏的模型回执和该 JSON 的 SHA-256 关联。

SDK 离线回归只用明确导入的合成供应商响应与 `MockTransport`，不读取真实密钥、不调用真实模型：

```powershell
.local/agentscope-venv/Scripts/python.exe -m unittest discover -s scripts/quality/business/tests -v
```

没有 SDK 的环境会跳过原生 SDK 适配测试，保留计划/请求/保密边界测试；跳过不能算作真实 SDK 测试通过。

## 测试有效性与负控

执行同一计划五次：

1. `baseline`：未经修改的真实业务逻辑，必须全部通过。
2. `sham_wrapper`：无语义变化的转发包装，必须全部通过；防止测试因包装本身误报。
3. `contact_get_ignores_tenant`：明确的 **synthetic mutation**，在子进程内让联系人读取忽略组织。
4. `duplicate_phone_guard_removed`：明确的 **synthetic mutation**，在子进程内绕过重复手机号查询。
5. `viewer_rank_elevated`：明确的 **synthetic mutation**，在子进程内错误提高 viewer 权限。

三个错误仅改变隔离进程的内存对象，测试结束即恢复，磁盘源码不变。每个变异必须由“基线通过且变异失败”的固定业务断言杀死；请求异常本身不计入有效杀死。报告保留所有场景与失败。最小复现选择已执行失败场景中请求最少的一项，使用固定夹具、场景 ID、源码和 oracle SHA 重放；不声称已经证明全局最小。

## 输出及门禁

- `report.json`：`schema_version=1`，完整五组执行结果、固定断言、源码清单 SHA、实际依赖版本、请求量与边界。
- `regressions.json`：每个变异的复现、重放计划、源码/oracle SHA。请求仅保存方法、路径、身份别名、请求长度/散列、字段结构及响应摘要，不保存正文、手机号或完整响应；分页游标签名也不落入产物。
- `summary.json`：`passed`、`baseline_passed/total`、`negative_control_passed`、`synthetic_mutants_killed/total`、报告与源码 SHA。

仅在基线、负控均通过、三种变异均被有效杀死且源码前后不变时，进程退出码为 0。失败、超时、依赖缺失或超限都退出 1，保留失败报告。总进程时限默认 120 秒、允许 10–180 秒；最多 512 个请求，每请求 3 秒、请求 64 KiB、响应 256 KiB、最终 worker 结果 1 MiB。原应用日志不进入测试产物。

子进程通过环境白名单去掉业务、模型与代理凭据，并拦截网络及常见 dotenv 文件读取。这些是避免测试误连外部依赖的护栏，不是可执行不可信代码的 OS 安全沙箱。

测试工具不修改或部署 CRM，也不向 Drop 生产创建任务。实际生产发布和完整数据库链路需要另行审阅与验收。
