# Go 安全门禁、诊断取消与部署彩排（2026-09-30）

本次完成面试版本的依赖安全更新、Go阻断门禁、诊断取消入口和正常业务链路彩排。四服务已部署到`20260930T115734Z`，源码`f9b143ae3dfa4409ed621d34b5d2971e90c0a972`。[CI 36710634578](https://github.com/llongwang751-arch/mini-drop/actions/runs/36710634578)成功13/13。**额外三段延迟比较失败，严格21故障与小时实验的旧失败成绩保留。**

## 交付与验证

| 项目 | 实际结果 | 原始证据 |
|---|---|---|
| Python全量 | 1217 passed / 10已登记skipped；PG与Chroma另有零跳过专项 | `pytest-full-final.xml`、`ci-python-quality.zip` |
| PostgreSQL | 8 passed / 0 skipped，包含并发取消和迟到Analyzer提交；独立tmpfs复验8/0，测试容器已移除 | `postgres-race-after.xml`、`ci-postgres-concurrency.zip` |
| Web | 44文件 / 226测试通过、构建通过；真实Chrome取消和报告页面通过，无JS/HTTP错误 | `web-full.log`、两个`browser-*/result.json` |
| Go | 本机测试与vet通过；CI Linux race、source/binary漏洞门禁、lint均通过 | `go-final.log`、`lint-final.log`、`ci-go-security.zip`、CI作业记录 |
| 发布 | Worker/Analyzer各184文件、Web45构建文件、API二进制SHA核对一致，四服务healthy，其余9容器未重建 | `deployment.json`、`source-verification.json` |
| 收尾状态 | API三个依赖healthy，13容器运行，21故障全部inactive | `final-health.json` |

CI PostgreSQL记录的测试merge为`7aa75e40397cbec074c9a28cd46a4d82bc620e81`；来源PR head是上述f9b143a。后续归档提交不冒充运行时源码。证据目录为[`reports/quality/security-cancel-20260930`](../quality/security-cancel-20260930/)，61份文件由`evidence-manifest.json`登记SHA与大小，六个CI原始ZIP另有下载摘要。

## Go安全与lint

Go最低工具链升到1.26.8，API与demo CI读各自go.mod；Docker构建同步版本。主要升级pgx5.9.2、gRPC1.83.2、x/crypto0.56.0、x/net0.58.0、compress1.18.7，以及对应传递依赖。Go按两个最新大版本维护，[官方发布记录](https://go.dev/doc/devel/release)支持本次选择。

同一govulncheck1.8.0、同一数据库快照（last_modified `2026-09-28T16:43:40Z`）下，升级前Windows/Go1.26.5扫描有47个模块版本公告，46条函数轨迹涉及11个公告；升级后Windows与Linux源码扫描均为**0条函数轨迹、0个可达公告**。这里的46条不是46个可利用漏洞。原模块层OpenPGP公告`GO-2026-5932`仍保留1条，源码与最终二进制没有导入或调用该包。[官方公告](https://pkg.go.dev/vuln/GO-2026-5932)说明该包无已知修复版本，本次没有屏蔽公告。当前Trivy全仓报告API仅1 UNKNOWN版本命中，没有CRITICAL/HIGH，仍不把全仓或完整镜像安全说成已完成。

CI源码JSON用于归档，随后使用普通文本模式的退出码阻断；JSON模式返回0本身不是安全通过。Linux二进制也独立扫描并保留build info和SHA。首次本地候选使用`-s -w`剥离符号，binary scan报OpenPGP通配符命中；核对[扫描器源码](https://github.com/golang/vuln/blob/v1.8.0/internal/vulncheck/binary.go)确认stripped路径退回模块级精度。最终改为`-w`保留符号表，二进制扫描0可达、模块公告仍可见，没有豁免规则。部署二进制SHA为`ffb7bc11e5c7dbaa7165c717af90ecd726cd4cad6ebf1794f75602630128d2b3`，容器内摘要相同。build info的`vcs.modified=true`来自本地未提交教程改动；发布包的Go与运行源码均取已提交版本并逐文件核对，不把该字段隐藏。

golangci-lint升级到2.14.0并阻断，13条存量告警清到0：SSE写入错误结束响应、只读Body关闭采用明确清理方式、移除未调用分页函数、简化cron判断；测试Body关闭同步处理。全仓Trivy仍report-only，尚无完整容器OS扫描。CI绿色仅说明列出的阻断规则通过。

## 取消合同与竞态

公开接口为`POST /api/v2/diagnoses/{diagnosis_id}/cancel`，请求reason可省略，去空白后1–512字；expected_version可省略，提供时>=1。额外字段拒绝。operator/admin允许，viewer/approver和资源受限V2账号拒绝。缺失或归档404，首次版本冲突或另一终态409，输入非法422。CANCELLED重复请求返回原终态，即使仍携带取消前版本也不会重写理由、操作者或版本。

同一事务锁父会话、关联工具、AnalysisJob与Task，取消活跃任务、撤销Analyzer租约、释放预算，提交唯一`diagnosis.cancelled`事件和outbox。已完成产物、证据和报告保留。规划/工具执行采用父会话优先的锁顺序，审批、报告生成和Evidence导入复核取消状态；Analyzer完成事务先锁job再锁task，取消胜出后不写迟到产物。同步Analyzer不能立即终止CPU计算，持久化权限会被撤销。诊断取消不等于撤销故障注入，后者仍需单独停止恢复。

旧源码11项负向回归真实失败并归档；修复后全量和两项新增真实PG竞争通过。PG竞争不是SQLite串行测试：取消事务持有job锁时，迟到完成线程明确阻塞；取消提交后它读取CANCELLED，Task不恢复，产物数量仍为0。竞争重复取消只增加一个版本和一条事件，迟到假设拒绝，历史报告保持原内容。

真实Chrome先打开确认框再点击停止，页面显示“只读记录”；原版本重复请求仍只有一个取消事件。另取AGI进程启动60秒sys_metrics，确认Task已RUNNING后取消。该Agent stdout有缓冲，25秒内未读到新`task_finished`不作为采集未退出的证明；改用Agent主循环中“join上一采集线程→清busy→领取新任务”的实际约束核验：**10.293秒内领取后续5秒任务，后续任务DONE**，原Diagnosis/Task仍CANCELLED、无迟到Report。10.293秒含网络、创建新会话和心跳，是退出上界，不是精确退出耗时。

## 发布失败、回滚与复验

首次发布`20260930T114936Z`的API因Windows打包丢失ELF执行位而permission denied，流程自动恢复四服务旧镜像并恢复current指针；失败日志保留。新包在tar中明确0755，候选镜像先验证`test -x`与SHA，再发布`20260930T115734Z`。旧发布、镜像、私有rollback.compose.json与数据库/对象数据均保留；不清理“orphan”容器。运行时容器OS基础层沿用旧镜像，本次只证明Go产物安全扫描和应用源文件身份。

## 面试彩排：正常链路通过，额外性能比较失败

新正常AGI问答答案符合既有人工测试文档，request_id为`8a85fc5af55c47cb9499f9e2279216d2`，总耗时4603.908ms、检索1538.016ms。同request_id关联Diagnosis `insight_2a97fc90606d4379821e4c9ddaf75ac6`；1个工具完成、2条Evidence、1份Report，2份产物下载SHA一致。当前真实页面展示关联请求、进程指标和探索树，报告为`INSUFFICIENT_EVIDENCE`。后续采样属于新的观察窗口，不能用来重建原请求调用栈。备用[报告与树短录屏](../quality/security-cancel-20260930/browser-rehearsal/interview-report-tree.webm)只录制报告/树页面，业务请求与链路另有API证据，不能称为全网页上传/问答录像。

额外三段请求均COMPLETED、答案正确，注入标记分别0/2500/0ms；检索耗时分别7304.892/7616.624/1987.783ms。故障减正常仅311.732ms，**没有达到原要求+2000ms，验收FAIL**。不修改阈值，不以另一时段正常请求1538.016ms当作优化收益；现有证据不足以把波动归因给网络、数据库、模型或进程。下一项有价值的工作是对同负载窗口补齐检索细分阶段、资源与独立对照，稳定实验后再谈修复收益。

最新完整21故障仍严格根因0/21、观测4/21，历史小时19,950请求成功但60RPS及9/120窗口延迟超限；本次不重跑或覆盖这两份历史成绩，也不把取消/正常彩排当作因果根因或同负载代码修复的证明。其他边界见[`REMAINING_WORK_20260930.md`](../../docs/REMAINING_WORK_20260930.md)。
