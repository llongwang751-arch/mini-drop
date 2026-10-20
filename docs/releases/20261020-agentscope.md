# 2026-10-20 · AgentScope 原生 ReAct 接入与上线

已部署至 [Drop](https://82.157.81.79/investigations)：API `20261020.12`、AgentScope runtime `20261020.2`（官方SDK `2.0.10`）、Web10、Agent/Helper3、SQL8。27个运行容器、7个监控目标up。只切换API与新增框架服务，原采集/数据/Web实例及全部旧镜像、数据卷保留。

## 新的真实网页闭环

调查11、任务86→87、诊断29→30、复测14、源码修复9。75.6秒到成功状态，包含最终双视口/录像收尾共76.4秒。规划、两次诊断、源码修复均由真实AgentScope Agent执行，使用服务器配置的DeepSeek；没有拦截请求或导入模型回答。

| 阶段 | 模型调用 | 工具回调 | 框架执行秒数 | 公开运行UUID |
|---|---|---|---|---|
| 规划 | 2 | 2 | 3.513 | `18d468dc-2ee1-4e7a-ad67-19b481c64168` |
| 原诊断 | 4 | 6 | 9.597 | `0c3cba8e-b583-435d-bf93-282597bddb2b` |
| 复测后新诊断 | 3 | 4 | 8.053 | `317da541-ba7b-472b-b865-059bcc011f97` |
| 源码修复 | 4 | 5 | 14.777 | `e42aee22-4a63-42e1-a3d2-eb2127083fae` |

合计13次模型调用、17次实际工具回调；Go另提供两次诊断的初始证据。provider用量输入74264、输出4116 token。复测后的新诊断实际调用compare_profiles；重复采样仍不宣告根因。框架/版本/类与计数来自控制进程，模型伪造这些字段会被拒绝。

修复对真实Saber FTS5模块的build_fts_query提出最小算法优化：中文片段判别及顺序去重达到64词元后提前停止。实际原2项测试、冻结6项测试、384例严格类型/值/SQLite结果对照均通过，6/6组交替CPU对照改善，中位比1.558688。profile和test_patch均cache_hit=false。结论只覆盖登记组件与合成长查询基准，不能解释为整个Saber或生产P95/P99提速；原业务源码没有自动发布。

1440桌面和375手机页面实际查看通过，无页面错误或横向溢出；关联链接直接选择本次Saber修复9，没有刷新或手动挑历史记录。[完整收据](../evidence/agentscope-20261020/acceptance-saber-live-2026-10-20T11-06-25-677Z/summary.json) · [详情](../evidence/agentscope-20261020/acceptance-saber-live-2026-10-20T11-06-25-677Z/detail.json) · [录屏附件](https://github.com/llongwang751-arch/mini-drop/releases/download/v2026.10.20-agentscope/agentscope-saber-success.webm)。

历史Go调查9在同源码、同类目标、10秒99Hz/native=false参数下共88.0秒，CPU比1.353364。本轮76.4秒/1.558688是不同时间、不同模型补丁的一次观测，**不是AgentScope使框架更快或优化更强的因果实验**，也没有运行LangGraph性能对照。

## 真实失败与工程修正

- 初次启动时root权限初始化容器不属于GID10003，Linux清除2750的setgid位，runner严格检查拒绝启动。旧API保持健康；仅给临时init增加supplementary10003，保留能力白名单和消费者只读挂载。实际Linux双目录2750验证通过。
- fresh PG最初暴露Go工具schema里的required为[]string、校验库要求JSON值树；归一化后仍严格执行required校验。首轮失败记录保留。
- 网页调查10/修复8虽然补丁测试通过，最终报告未被采纳，完整验收明确失败。随后在同一真实Go/SDK/双UDS测试包复现旧runtime数字重编码会触发accepted_result_mismatch；新runtime原样复制Go已校验JSON token，保留指数、负零和长小数，Go严格结果校验没有放宽。旧修复8只有泛化错误，不能事后把其单次原因唯一归为这个问题；新调查11完整通过证明当前路径恢复。
- 私有错误响应仅允许固定错误码、阶段/异常类别及预算内计数；结果不符、metadata不符和非200分别记录安全原因，不记录异常原文、模型正文、参数或凭据。
- 源码broker使用create→验证job label和固定镜像→按不可变CID start；取消/超时只停止自己的容器。Linux真实Docker EOF到清理约0.58秒，SDK/Go取消fixture约0.53秒，取消后下一会话通过。没有把fixture当成真实云模型故障注入。

## 检查与资源

| 检查 | 实际结果 |
|---|---|
| Go全包测试、vet、Linux API构建 | 通过；fresh PG另列，普通本机单测中缺数据库的skip不冒作集成成功 |
| fresh隔离PostgreSQL17 | 117通过、0失败、0skip；含真实DeepSeek导入fixture，其他协议/存储输入明确为fixture |
| 官方SDK Linux回归 | 33通过、0skip，包含真实UDS/peer/断链与大JSON精度 |
| Go↔SDK双UID互通 | 同包旧1数字负控失败；新2八场景通过，含纠错、预算、取消和恢复 |
| Go真实Linux peer回归 | 2通过 |
| 源码执行器 | 12回归通过；实际Docker正常退出/EOF回收通过 |
| Compose | 5通过，28个真实追加负控被拒绝；无本机daemon启动 |
| 公网网页 | 上述全新真实Saber流程与两视口通过；第一次失败保留 |

本轮没有修改C++采集器或Web代码，先前Core3 Werror/18项、IPC8及16类Runner/20真实任务证据保留，未声称本轮重新采样全部Runner。

独立runner有94个Python distribution；依赖严格版本和官方wheel SHA256锁定。实际单机进程RSS快照：空闲107.0MiB，真实流程后115.0MiB；限额512MiB/0.5CPU。镜像inspect Size为112117290B，是当前Docker后端的计数，不能与共享层相加当作实际增量磁盘。kernel没有memory.peak，**这些快照不是运行峰值**。AgentScope不是零依赖；没有据此宣称比LangGraph更省资源。[资源证据](../evidence/agentscope-20261020/resource-after.json)。

最终备份 `drop-20261020T110823Z-1595d4` 用时33.94秒，真实隔离PG与S3恢复通过，579制品/579对象核验、87任务/9修复/11调查保留；是同机恢复证据。[备份收据](../evidence/agentscope-20261020/final-backup.json) · [部署](../evidence/agentscope-20261020/deployment2.json) · [健康](../evidence/agentscope-20261020/readiness.json)。源码与证据归档另记delivery收据，不覆盖历史包。

[接入与责任边界](../AGENTSCOPE_INTEGRATION.md) · [面试操作](../INTERVIEW_DEMO.md) · [交付矩阵](../DELIVERY_MATRIX.md)。
