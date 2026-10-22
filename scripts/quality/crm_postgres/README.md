# CRM真实数据库、RLS与本地认证适配验收

本工具把已有联系人ASGI测试接到原始CRM SQL和真实PostgREST，不重新造联系人表或RLS策略。需要用户指定的私有Python CRM源码，以及原仓库的 `supabase/baseline.sql` 和 `tests/supabase-prelude.sql`。后者是明确的Supabase auth/storage兼容夹具，不是真正部署GoTrue或Storage。

## 真实范围与身份边界

普通请求经过实际FastAPI路由、`crm.auth.get_verified_session/_resolve_identity`、真实成员角色查询、`SupabaseRepository`、PostgREST JWT验证及PostgreSQL RLS，不使用 `identity_resolver` 直接授予身份。认证适配器在容器回环HTTP提供 `/auth/v1/user`，严格验证本地HS256签名、issuer、audience、exp/nbf、subject和合成会话绑定。它返回用户身份，不返回业务组织或角色；后者由真实PG实时查询。

**这是本地issuer认证适配验证，不是外部GoTrue/企业SSO/MFA/真实账号登录验收。** JWT、会话、密码、组织和联系人均为一次性合成夹具。普通PG连接使用 `LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS` 的专用authenticator，再切换到真实 `authenticated` 角色，检查角色标志和 `row_security=on`。管理员只用于装原SQL、构造/复位夹具及快照，不执行应用权限断言。

覆盖内容包括：

- 不带租户WHERE的真实查询仍由原RLS隔离，跨组织插入被数据库拒绝。
- 显式事务中的插入/修改及业务副作用真实回滚。
- 经原CRM `ConfigTransaction` 创建pipeline和stage后注入预定异常，两表完整行摘要必须恢复；注入入口仅为测试夹具路由，不添加到CRM源码或生产端点。
- 两个独立应用连接并发写同组织相同手机号，由原唯一索引裁决为一成功/一23505；不同组织同号是成功对照。
- 短暂关闭此可销毁库的contacts RLS作为 **synthetic mutation**，越权断言必须杀死，finally恢复RLS。
- 新生成DSL数据/操作序列通过真实业务链执行；拒绝请求前后比较联系人、事件、任务和活动的完整行摘要。
- 缺JWT、错签名、过期JWT及真实PG成员身份撤销均拒绝访问。

授权拒绝可以追加明确的安全审计记录；“无业务副作用”不要求删除正常拒绝审计。SQL回滚验证不代表所有CRM多HTTP写操作已经成为一个原子事务。

## 本机离线检查

```powershell
python -m unittest discover -s scripts/quality/crm_postgres -p 'test_*.py' -v
.local/agentscope-venv/Scripts/python.exe -m unittest discover -s scripts/quality/business/tests -v
```

离线测试验证本地签名/时效/会话、HTTP适配、DSN/域名白名单、DSL及独立oracle，不代替实际PG/RLS验收。SDK测试使用明确合成的MockTransport，不调用真实模型。

## 准备私有快照

先生成或提供schema_version=2的DSL用例，不再局限47个catalog ID：

```powershell
python scripts/quality/business/run_generated.py --emit-deterministic-suite .local/crm-new-suite.json
python scripts/quality/crm_postgres/package.py --crm-source E:/crm/AiNativeCrm-python --suite .local/crm-new-suite.json --output .local/crm-pg-private-bundle
```

快照只含允许的Python源码、两份原始SQL、工具和合成DSL，并记录每个文件SHA；原源码前后必须不变，不读 `.env` 或业务数据。该目录和压缩包只留本机/受控服务器私有路径，不得提交Git或上传公开镜像仓库。

## Linux容器执行

操作员显式准备缓存镜像。工具本身禁止自动拉取镜像，并记录解析后的不可变image ID。以下默认引用由root准备，镜像供应链与具体digest需在实际回执核对：

- `drop-crm-sandbox:20261020.4`：已有实际CRM Python依赖，入口 `/opt/drop-tools/venv/bin/python`。
- `pgvector/pgvector:pg17`：支持原基线vector/citext/pgcrypto等扩展。
- `postgrest/postgrest:v13.0.8`：真实JWT/RLS访问层。

```bash
sudo python3 scripts/quality/crm_postgres/run_isolated.py \
  --bundle /opt/drop-quality/private/crm-pg-bundle \
  --output /opt/drop-quality/private/new-crm-pg-run
```

只创建带随机名称/标签的新内部Docker网络与三个新容器，不发布主机端口、不挂业务卷/PID/socket。测试CRM进程UID10001，只读根目录、去capabilities、512MiB/0.5CPU。密码只通过临时私有配置/env文件传递，不出现在命令参数、日志或公共回执。清理前再次核对本轮资源标签，只删除本轮容器及其匿名测试卷，不删除既有镜像/卷。

host编排兼容Python3.10，只使用标准库及本工具的DSL校验器；实际CRM worker使用缓存镜像内Python3.12。worker先装原SQL、准备真实登录角色并完成SQL检查，写本轮小型 `bootstrap-ready.json`，host验证后才启动PostgREST。worker提前退出或标志超限不会启动REST。首轮真实执行暴露了REST早于角色创建即退出的问题，失败产物保留；这个就绪握手及对应负控覆盖了该编排故障，没有放宽身份/RLS/oracle。

`acceptance.json` 保留实际SQL、认证和新序列结果与失败；`run-receipt.json` 记录镜像ID、源码/DSL/报告SHA、退出状态和清理结果。根agent可导出安全公共摘要，原SQL/源码、JWT、连接串、请求和响应正文均不得进入GitHub证据。
