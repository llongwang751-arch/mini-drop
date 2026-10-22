# JD补强：真实HTTP授权与确认矩阵

`api/internal/httpapi/jd_security_matrix_integration_test.go` 使用真实 `Server.Router()`、真实HTTP处理器、真实仓储与随机隔离PostgreSQL schema，运行权威迁移。它不是复制API的模拟服务，不连接生产、不调用模型或采集器。

每个拒绝请求都检查HTTP状态及稳定错误码，并逐一比较18个领域表的完整JSONB行快照，涵盖状态、事件、outbox、分析/诊断/补证、源码修复、分享、方法和审计。仅比较行数会漏掉“未授权确认把proposed改成queued”，所以这里比较全行。认证防滥用的 `rate_limits` 允许按设计更新，不算业务副作用；会话失效由真实sessions表验证。

场景覆盖缺失/畸形/未知/过期会话、缺失/错误CSRF、跨所有者读写、只读分享越权、未确认/未审阅采集、目标替换、过期/不存在/未授权下载，以及只读角色建任务。所有者和只读分享的成功读取作为正对照。拒绝路径不需要对象存储：如果权限被绕过而尝试签发下载，测试会因真实处理器失败或业务快照变化而失败，不能以伪造的下载服务掩盖。

独立数据库可先由本机隔离夹具启动，显式设置 `TEST_DATABASE_URL` 与 `DROP_JD_ISOLATED_POSTGRES=1`，再执行：

```powershell
python scripts/quality/security/run.py --output .local/jd-quality/security-matrix.json
python -m unittest discover -s scripts/quality/security -p 'test_*.py' -v
```

runner仅允许loopback的 `postgres`/`drop_test`/`drop_jd_test`，Go按随机schema隔离并Cleanup，执行超时180秒。没有真实PG导致skip时收据明确失败；不能把跳过视作通过。公开收据只输出测试名/计数/日志SHA，连接串和请求正文不会复制进去。

该矩阵证明应用路由的明确边界，不等于动态漏洞扫描、TLS/反代安全、浏览器Cookie策略验证或完整渗透测试。这些边界与现有SAST、依赖扫描、C++隔离及受控故障回归分开报告。
