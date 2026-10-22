# CRM 业务生成测试与真实数据库验收

本轮已把Python CRM联系人测试接入真实PostgreSQL、原始SQL、PostgREST和本地issuer认证适配，同时增加能产生新合成数据与操作序列的AgentScope入口。最终模型套件在真实CRM/Memory链和真实数据库链均通过。范围是已审阅的联系人操作及明确的事务故障夹具，不能写成“全CRM、外部认证、任意业务自主探索全部通过”。

公开证据只有聚合计数、检查名称、版本和散列：

- [模型生成与Memory执行](evidence/jd-depth-20261023/business-generation.json)
- [三轮真实数据库记录](evidence/jd-depth-20261023/business-postgres.json)
- [SDK、边界回归与工具冻结](evidence/jd-depth-20261023/business-validation.json)

原CRM的394个Python文件、原SQL、私有快照、请求正文、身份、手机号、JWT、连接串和凭据均不进入公开证据或GitHub。

## 模型生成了什么

原来的47项目录选择入口仍保留。新增的 `generate_cases_with_agentscope.py` 使用官方AgentScope2.0.10、DeepSeek和唯一 `generate_business_cases` 工具，接收审阅需求与受限JSON DSL Schema，可以创造新数据以及create/read/patch/list/delete序列，无需引用47个固定场景ID。

模型不能提供URL、SQL、可执行代码、断言或预期状态。权限、字段规范化、状态变化与拒绝后无业务副作用由独立 `independent_oracle.py` 判断，不调用CRM schema或服务来生成正确答案。创建响应只提供资源ID供后续引用，不能把返回字段复制为oracle预期。新增领域仍需单独审阅需求并扩展oracle。

入口固定模型端点和严格TLS，最多3次模型、1次工具、60秒、不重试，输出最多4000tokens。密钥只从显式受保护文件有界读取；执行器不持有模型密钥。实际生成有两次独立尝试，不能合并写成一次：

| 尝试 | 实际模型/HTTP/工具次数 | 结果 | 供应商input/output tokens |
|---|---:|---|---:|
| model-001 | 1 / 1 / 0 | `PLAN_OUTPUT_REJECTED`，失败保留 | 5746 / 1072 |
| model-002 | 1 / 1 / 1 | 新4个case、17步，接受原生工具提交 | 6482 / 746 |

首次未保存供应商原文，也未记录细分拒收码，因此具体拒收原因不可归因。随后独立发现工具Schema未按操作声明全部必填字段，与执行器精确字段校验不一致；修正Schema、补角色与别名结构说明，并增加固定枚举拒收码和输出渠道。没有放宽DSL、oracle或非法输出接收条件，不能据此声称已证明首次失败原因。成本回执均为 `null`，没有估算费用。

最终生成套件的逻辑SHA为 `ee6408838a950bf85e004c302206efaf19171fd6986516e563e3731658cf093c`，原生成文件SHA为 `96df64eff0aa4476c973c2b796aa46132b6cf39754d7921d868f01f417f686cc`。模型Schema、上下文、审阅需求、独立oracle及原源码SHA分别绑定在证据中。

## 两条执行链与请求分母

Memory链实际执行原FastAPI路由、Pydantic校验与业务服务，使用MemoryRepository和明确的合成身份解析器。最终模型套件baseline为4/4，sham为4/4，三种synthetic mutation均有基线通过后业务断言失败的有效杀死证据，五组共76次ASGI请求。跨租户读取、重复手机号与viewer拒写的错误只注入隔离进程，不能称为发现三个真实漏洞。

数据库链的调用关系为：

```text
实际FastAPI路由（ASGI）
  → 原CRM会话校验、实时成员角色解析
  → 本地issuer回环HTTP / 原SupabaseRepository
  → 实际PostgREST JWT校验
  → 实际PostgreSQL、原表/唯一索引/RLS
```

CRM入口仍由ASGI调用，后续认证适配和数据库访问使用真实内部HTTP/网络；这不是公网CRM的TCP/TLS端到端验收。最终模型数据库轮执行17次生成ASGI请求，内部网关累计132次HTTP请求，包含成员解析、鉴权和仓储访问。**Memory的76与数据库网关的132是不同分母，不能相加或直接当作性能对比。**

## 数据库、事务与认证检查

每轮完整验收重放同一组8项数据库检查，固定与模型轮不是16种不同数据库检查：

1. 普通应用连接切换真实 `authenticated` 角色，核对非superuser、无BYPASSRLS及 `row_security=on`。
2. 不带租户过滤的SELECT仍由原RLS限制为本组织记录。
3. 跨组织INSERT返回SQLSTATE42501，业务表摘要不变。
4. 显式SQL事务内插入、修改后注入异常，联系人、事件、任务与活动表摘要恢复。
5. 两个独立应用连接并发写同组织相同手机号，原唯一索引裁决为一提交、一23505，最终一行。
6. 不同组织相同手机号成功，作为唯一约束的正对照。
7. 在可销毁库短暂关闭contacts RLS作为synthetic mutation，越权断言必须检出，并在finally恢复RLS。
8. 经原CRM `ConfigTransaction` 创建pipeline与stage后注入异常，两表摘要恢复。入口是明确的测试夹具路由，不添加到CRM源码或生产端点。

原 `supabase/baseline.sql` 与原 `tests/supabase-prelude.sql` 按字节导入，SQL SHA留存；后者是明确的Supabase auth/storage兼容夹具。管理员仅负责装SQL、构造/复位合成夹具与只读摘要。普通联系人权限断言使用真实非superuser、无BYPASSRLS应用角色，后台审计等系统操作保留原CRM服务角色路径。

同一组4项认证检查分别验证缺JWT、错误签名和过期JWT返回401，以及真实PG撤销成员身份后返回403。拒绝前后比较联系人、事件、任务、活动表摘要；授权拒绝允许追加明确的安全审计记录。

本地issuer严格校验HS256签名、issuer、audience、exp/nbf、subject和合成会话绑定，只返回用户身份；组织及角色由实际CRM查询真实PG获得，未用 `identity_resolver` 直接授予身份。PostgREST也验证JWT。**这不代表外部GoTrue、企业SSO、MFA、真实账号恢复或完整认证系统已经验收。**

## 三轮服务器执行与保留失败

所有时间为实际收据UTC；北京时间加8小时。私有bundle版本与执行轮次分别记录，不能混用：

| 私有bundle / run | 套件 | 实际开始UTC | 实际结束UTC | 结果 |
|---|---|---|---|---|
| 003 / crm-run-001 | 确定性新DSL | 2026-10-22 18:10:53 | 18:11:37 | 失败于 `wait_original_postgrest`，保留 |
| 004 / crm-run-002 | 同一确定性新DSL | 2026-10-22 18:15:20 | 18:15:42 | 8/8数据库、4/4case、4/4认证 |
| 005 / crm-run-003 | 真实model-002新DSL | 2026-10-22 18:16:45 | 18:17:08 | 8/8数据库、4/4case、4/4认证 |

首轮PostgREST早于临时登录角色创建而因认证失败退出。修复后的worker先装原SQL并准备角色，写本轮小型bootstrap-ready标志，host验证后才启动PostgREST；worker提前退出或标志超限不会启动REST。该启动顺序有明确回归，未放宽身份、RLS或oracle。host兼容Python3.10；实际worker使用缓存镜像内Python3.12。

测试只创建新内部Docker网络、新PG/PostgREST/worker容器和本轮匿名测试卷，不发布主机端口；使用只读worker根目录、UID10001、去capabilities及内存/CPU限制。三轮所有owned容器和网络均已清理，既有镜像和命名业务卷保留。

首轮控制器直接比较未规范排序的卷列表，记录过false；独立规范化复核中29个生产容器ID、镜像ID与排序卷名全部相等。首轮未保存原after列表，无法严格还原那次差异，原false与后续复核分别保留。后两轮在开始/结束即使用规范化比较，均严格相等，前后SHA留存。测试没有操作生产Drop服务或真实CRM业务。

## 回归与可复现边界

最终源码冻结后的实际离线回归为官方SDK环境业务测试38项、PG适配/编排测试10项，均通过且无跳过；SDK测试使用MockTransport合成供应商响应，没有增加真实模型调用。Ruff检查及格式检查通过。原始检查日志只保存在私有产物，公开收据记录日志SHA和起止时间。收据解析曾因Windows CRLF把退出码0的测试误标为false，原收据保留，按原日志splitlines规范化复核为38/10通过，没有重跑挑选结果。

最终工具文件逐一散列与模型005私有bundle一致。原CRM源码前后不变，原源码清单SHA为 `3e7fefb4d7fd5220ee3d23efb381e574eec08952372341884e97c97ac003beb6`；独立oracle源码SHA为 `3ab09c5d861bb67de2b0271d70e364da7115a463623ca79cd6ced83ed3b95a0b`。实际执行使用缓存镜像的不可变image ID，完整绑定见公开JSON。

运行方式见 [business工具](../scripts/quality/business/README.md) 与 [crm_postgres工具](../scripts/quality/crm_postgres/README.md)。完整数据库复现需要用户私有CRM源码及原SQL，公开CI不会取得或假称已执行这些私有业务。公开CI可运行SDK离线、签名/白名单及编排边界回归；真实数据库结果以本页绑定的服务器收据为准。
