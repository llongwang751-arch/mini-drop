# 全量交付与验收矩阵

## 云蓝与冷灰配色（2026-10-21 候选）

按用户反馈统一指南及共用侧栏/按钮/登录图的配色，布局和业务行为保持；同时修复发现的移动登录H1缺失，视觉不变。新构建、Prettier、既有UI20/20（0skip）及桌面/手机登录检查通过，合计八次axe扫描零违规；主按钮白字5.82:1、辅文在背景4.73:1。成功/错误状态与火焰图编码保留，36份历史证据SHA不变。候选仅更新Web至20261021.1；实际发布及公网验收完成后另附本轮证据，不把上一版Lighthouse或现场采集记录算作本轮重跑。见 [本轮回归](evidence/palette-20261021/regression.json) 与 [登录检查](evidence/palette-20261021/login-visual.json)。

## 使用指南增量（Web11 已上线，2026-10-20）

`/guide` 已重构为与 Drop 品牌一致的交互式技术手册，包含采集→分析→诊断→验证、场景索引、结果边界和按权限显示的操作入口。六宽度 UI15/15、原导航回归16/16、Web137、构建及 Prettier 实际通过；四宽度流程切换位移0px、axe四次0违规。Lighthouse本地导入夹具桌面99、手机91，可访问性/最佳实践100；不作为生产响应时间或真实采集/模型执行证据。见 [设计与八维复审](../design-system/drop/pages/guide.md)、[最终页面验收](evidence/guide-20261020/final/summary.json)、[导航回归](evidence/guide-20261020/final/navigation-regression/summary.json)、[性能范围](evidence/guide-20261020/final/lighthouse.json)。

CI原fixture opt-in已显式启用，本机完整默认preview实际48/48通过、0skip、0fail。**上线完成**：功能提交a898c9d已推送master；实际只替换82.157.81.79的Web11，其余26容器身份及镜像保持，27运行健康、7监控up、HTTPS readiness200，API12、AgentScope runtime2、Agent/Helper3、SQL8及原镜像/数据卷保留。[部署收据](evidence/guide-20261020/final/deployment.json) · [真实认证公网GET检查](evidence/guide-20261020/final/production/summary.json) · [发布说明](releases/20261020-guide.md)。GitHub新运行Web job已成功，完整状态见本次CI观测，未将仍在执行的job预写为成功。

## GitHub 源码发布补充（2026-10-20）

用户已明确授权替换 `mini-drop` 的过时项目文件，使用完整 tx-drop 快照并保留原提交历史。新增官方 AgentScope runtime CI、8镜像手动发布矩阵、当前项目上下文和文档索引；生成 OpenAPI 补齐实际报告 `agent_runtime` 的只读有界 schema，76路由/91schema及Proto兼容检查通过，未修改生产运行时。CRM/Saber 导出改为必填源码/输出路径，4项合成目录边界回归通过；Web137、构建/Prettier、启动器8、增量打包5实际通过。GitHub Actions 状态以推送后实际结果为准，未将其预写为通过。

公开范围排除密钥、登录态、数据库、现场RAW和录像Git blob，保留显式toy格式fixture及脱敏验收摘要；录像通过Release附件提供。见[发布范围](GITHUB_PUBLICATION.md)。生产API12/runtime2及原数据卷/命名镜像保持，后续源码同步不重建容器。

发布收据：源码12fb28804f7d已普通快进推送master，旧目录从当前tree移除；服务器1548文件校验、27容器身份保持、ready200/7监控up通过；成功/失败录像均已上传且GitHub digest匹配。[GitHub验真](evidence/github-publication-20261020/publication.json) · [服务器绑定](evidence/github-publication-20261020/server-sync.json) · [录像附件验真](evidence/github-publication-20261020/release.json)。首次Actions暴露健康命令引号及Compose负数fixture可移植性问题，后续提交修正；这段记录不宣称新远端整套已通过。

31cb6b3已同步1551源码，Linux Compose2.40.3五项通过；远端API race/vet/漏洞检查、Web、SDK、契约、Core构建通过。MinIO上游容器拉取失败另修复为[官方同版本binary+固定SHA构建](../deploy/minio/README.md)，CI继续执行原PG/S3集成，不放宽测试；[生产binary只读SHA](evidence/github-publication-20261020/minio-binary-proof.json)与官方相同。生产存储镜像和数据未替换，新CI状态见Actions。

**最终功能提交797e9ec7e581验收完成**：[push/master CI run39418233749](https://github.com/llongwang751-arch/mini-drop/actions/runs/39418233749) **7/7全部通过**，包含两个官方MinIO fixture构建、原Analyzer/Ops真实PG/S3集成、API race/静态及漏洞检查、Core、Web、SDK与契约。服务器1555源码已绑定；最终ready200/27容器健康/7监控up。[CI收据](evidence/github-publication-20261020/github-ci-797e9ec.json) · [最终健康](evidence/github-publication-20261020/final-readiness-797e9ec.json)。后续收尾提交仅文档/JSON，跳过重复CI；上述7项对应明确功能提交，不扩大为最终镜像统一安全扫描或长期稳定性验收。

更新：2026-10-20。最新为API12 / AgentScope runtime2（SDK2.0.10）/ Web11 / AgentHelper3 / SQL8，27运行容器、7监控up；调查11/修复9完整网页76.4秒通过，原2+冻结6、384例、6/6对照、CPU1.5587倍。Go117、SDK33、双UID互通8、executor12及Compose5/28负控通过。详见[AgentScope发布](releases/20261020-agentscope.md)与[接入](AGENTSCOPE_INTEGRATION.md)。后面的API10表格保留为采集器组合的历史验收。

历史组合部署：[https://82.157.81.79](https://82.157.81.79)，API10 / Web10 / Agent与Helper3 / SQL8，26运行容器、7监控目标up。用户点名的BOLT和malloc已通过；Saber真实FTS5组件已接入并完成最终网页全流程。详见[本次发布](releases/20261020-saber-collectors.md)。

| 当前增量范围 | 已执行证据 | 实际边界 |
|---|---|---|
| 所有实时Runner | 任务62–81，16类/20场景，采集→上传→分析→RAW大小/SHA通过 | 受控合成负载，工具/内核/目标配置依赖保留；原VFS/block历史证据另存 |
| malloc/free与memray | 任务63真100/100及200非空栈，memray62通过；3项最终隔离、peer正负控及sysfs拒绝通过 | 非mmap替代；SIGKILL后自动清残留未保证 |
| BOLT | 任务81，161样本、9制品、6组ELF输出一致；无分支数据UI明确未测量 | CPU比0.9942未观察提升，无LBR/ARM实机或自动业务二进制发布声明 |
| Saber源码与自动修复 | 调查9 / 修复7，88.0秒；2+6回归、384例、6组fresh对照，CPU中位1.3534倍 | 真实FTS5模块+合成输入，不是全部Saber上线；补丁在无网络隔离副本 |
| 回归与权限恢复 | C++18、IPC8、PG110(0skip)、执行器4、UI12+计数4通过；Agent10目标身份及目标恢复通过 | IPC/PG/UI fixture明确标记，不冒作生产故障注入或完整角色现场矩阵 |
| 平台与运维 | 任务50取消重试、DAG51→52、调度53；最终备份和真实隔离恢复；HTTPS200 | 单机同机恢复，无异地/HA或主机重启承诺 |

证据索引：[多场景](DEMO_SCENARIOS.md) · [Saber](SABER_INTEGRATION.md) · [现场演示](INTERVIEW_DEMO.md) · [剩余边界](CAPABILITY_GAPS.md)。历史失败/误判均保留，最终值以新记录为准。

## Agent V2早期基线（历史记录）

下表及其后原始验收属于API7/Web6/Agent1阶段。它们不覆盖上面的最终组合版本，也不限制新接入的Saber；当前状态以上表及本次发布收据为准。

| 范围 | 当前实现与实际检查 | 验收边界 |
| --- | --- | --- |
| 平台分层及契约 | C++/Go/Python/React独立；18 TaskKind、76 HTTP路由、90 Schema检查通过；SQL007/008真实迁移 | 18类型含导入/复合；不是18个现场采样器 |
| 从零自然语言采集 | 持久化会话、目标身份绑定、能力/schema目录、模型计划审阅确认；真实CRM任务11选择PYTHON_CPU、10s/99Hz | 专用端口/模板等缺失时需要配置，不捏造配置；不能任意shell或替换目标 |
| 真实采集与工具诊断 | CRM任务11得到987样本、258节点；DeepSeek诊断9实际检查热点/质量/多条调用链和复测草案 | 采样权重不是请求延迟；此前PROC_CPU/PERF_CPU现场证据仍有效 |
| 自动复测与再次诊断 | 确认后自动任务12、诊断10、compare_profiles，调查4关联源码修复4并完成 | 原采样987不足描述门槛1000，保留insufficient_evidence；重复采样不确认因果 |
| 源码定位及自动隔离修复 | 真实Python CRM408文件固定版本；cProfile实际行31/96/97等、文件SHA校验；模型补丁在隔离副本执行，原回归及冻结类型/隔离/身份/限流检查通过 | 首个preset为品牌配色；不代表全部CRM或任意仓库；没有自动发布原业务仓库 |
| 优化效果及负控 | 384次类型/值一致，6组交替CPU中位加速4.3343×、6/6达门槛，补丁后profile；错误list→tuple补丁拒绝，注释补丁比1.0540×、3/6，拒绝 | 合成业务函数的优化组合；不代表生产P95/P99，不是每行独立因果或正式显著性检验 |
| 权限/并发/恢复 | 隔离PG44项通过、0失败/跳过，含真实DeepSeek导入fixture；生命周期、幂等、取消、租约/重分析、自动复测、源码定位及降权历史报告隔离 | 协议mock与真实CRM/model证据分开，不能保证所有模型解释正确 |
| Web | 133单测、生产构建、8个浏览器fixture通过；公网8视图×桌面/手机16检查，axe0/脚本错误0/横向溢出0，截图已审阅 | 浏览器fixture不等于真实模型；真实结果另有公网记录 |
| 执行隔离 | API仅Unix socket；root登记源码/镜像，独立无网络/无凭据/只读/UID10001沙箱，512MiB/1CPU/64进程/240s硬限；运行身份不发模型 | 不是任意代码执行服务；取消限制后续写入，已启动计算在硬限内收尾 |
| 构建与部署 | 命名API7/Web6/CRM runner4及原镜像保留；schema8、运行镜像ID/二进制SHA、HTTPS、7个监控up见部署凭据 | 本轮未改C++，既有Werror+16CTest证据保留；未全重跑16类Runner |
| 备份与旧部署 | 迁移前PG快照；新写入后在线PG/S3备份与隔离恢复；旧drop-drop源码/容器已归档删除，数据卷及命名镜像保留 | 同机备份，未配置异地目的地；从未执行down -v |
| 剩余生产验收 | 既有受信任IP TLS、Secure Cookie、4组运维timer及有界helper spool继续使用 | 新镜像未统一CVE扫描/签名；没有跨信号拓扑RCA、全CRM覆盖、长期容量/HA/重启演练 |

证据：[真实闭环](evidence/agent-v2-20261020/live-crm-agent.json)、[摘要](evidence/agent-v2-20261020/summary.json)、[负控](evidence/agent-v2-20261020/negative-controls.json)、[PG](evidence/agent-v2-20261020/postgres-acceptance.json)、[公网](evidence/agent-v2-20261020/public/browser-acceptance.json)、[部署](evidence/agent-v2-20261020/deployment-proof.json)。见[实现及演示流程](DIAGNOSIS_AGENT_V2.md)、[公开产品/论文比较](AGENT_V2_RESEARCH.md)、[剩余边界](CAPABILITY_GAPS.md)。

[上版矩阵](history/DELIVERY_MATRIX_20261020.3.md)及[迁移前矩阵](history/DELIVERY_MATRIX_20261019.5.md)作为历史保留，不冒充其全部场景已在新版本重跑。旧120.24.187.205不再是演示目标。
