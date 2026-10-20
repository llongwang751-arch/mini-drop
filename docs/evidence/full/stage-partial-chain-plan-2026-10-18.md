# 当前失败执行的部分分析：真实stage验收方案（仅设计，未执行）

此计划不修改冻结运行源码、不写入伪造检查点，也不启动服务器、Docker或浏览器。根代理接受最终镜像安全门禁并明确交接`dropfullstage`后，才可由其执行容器协调。生产项目`drop`与数据卷不属于本计划范围。

## 已核对的实际路径

`analyzer/drop_analyzer/worker.py:process_job`先从真实对象存储下载并核验RAW，调用真实隔离解析器，再执行`Repository.save_partial`；之后才计算规则、构造/上传结果和调用`Repository.complete`。`save_partial`独立事务检查当前Task/最新采集attempt、job、run、owner、未过期lease和最新ready RAW id/digest，保存检查点与`ANALYSIS_PARTIAL_SAVED`事件。`complete`在独立事务内写入RESULT/MANIFEST元数据与成功状态；正常worker循环会将此事务的数据库异常归一为`ANALYSIS_INTERNAL_ERROR`，再经真实`Repository.fail`保留已提交检查点并提交失败状态。

`fail`是否重试使用领取时的`Job.max_attempts`。因此必须在claim之前设置本轮唯一fixture job的`max_attempts=1`，不能在检查点写入后再改DB来假定内存中的Job也会更新。重试会清空检查点，不适合用随机时间窗口碰最终FAILED。

## 最小故障：只拒绝本轮结果的最终元数据提交

使用一个随机命名的stage临时PostgreSQL trigger/function，只在`artifacts`的BEFORE INSERT且`NEW.task_id`、`NEW.attempt_id`等于本轮新fixture、`NEW.kind IN ('RESULT','MANIFEST')`时抛出固定SQLSTATE `23514`。不触碰RAW、事件、job失败写入、其他任务或原有表结构；不新增migration。故障发生在真实解析、检查点事务和S3上传之后，完整结果元数据事务整体回滚。

这是最后提交阶段的故障验收，不能据此宣称“未上传任何结果对象”。该job/run的少量已上传对象会成为未引用对象，须在证据中注明保留或仅按精确fixture前缀定向清理。不会更换对象存储角色/策略，不重分发S3凭据，不代理或模拟HTTP，不替换worker中的Repository、parser或upload方法。

Analyzer实现代理已独立只读复核此位置与事务语义，认为比专用S3拒写角色引入的变量更少。该判断不是实际执行结果。

## 顺序与最小脚本边界

建议新增两份验证脚本；目前仅设计，尚未创建或执行：

| 顺序 | 执行者与最小动作 | 失败边界 |
|---|---|---|
| 1 | 根代理按已锁定的`dropfullstage` labels/image ID确认PG、API、Web、Analyzer，保存生产非空运行容器IDs。只暂停stage Analyzer领取，记录原来运行状态；确认stage没有running分析job。 | 不操作生产，不重建镜像，不使用`down`，不接受仅按容器名的选择。 |
| 2 | `scripts/validation/stage_partial_chain.py prepare`在受限stage管理容器内读现有挂载的stage管理员/DB secret，新登录真实API并创建唯一`IMPORTED_STAGE_PARTIAL_<32hex>`小型folded导入任务。正文仅固定synthetic hot/cold权重80/20。 | 不接受既有task、任意origin、SQL、bucket或profile。响应有界且不输出cookie、URL、正文或DSN。 |
| 3 | 同一管理脚本锁该Task/job，要求创建者、唯一名称、最新attempt=1、job pending且attempt=0、ready RAW id/hash与刚上传正文相符；只将该job `max_attempts=1`。在同一准备事务内安装精确task+attempt触发器。 | 任一job已被领取则拒绝，不篡改status/result/lease；不将已成功任务改成失败。DDL名称使用随机32hex和安全Identifier，拒绝覆盖同名对象。 |
| 4 | 根代理恢复原stage Analyzer服务，仍使用实际冻结rc4镜像、原入口、原DB/S3配置及原资源边界。 | 不启动替代worker、不monkeypatch，不同时启动额外高负载。 |
| 5 | 管理脚本有界等待本fixture终态；从真实API和stage PG核对下列断言，并输出仅含IDs、摘要hash、计数及booleans的公共proof。 | 最长180秒；超时或错误仅固定code，不保留原异常/SQL或HTTP日志。 |
| 6 | 确认失败后立即移除本次自己创建的trigger/function；finally重复身份核对后清理，并恢复stage原暂停/运行状态。 | 对象不存在可记录已清理；名称存在但OID/表绑定/定义指纹不符时拒绝删除。超时也必须清理故障，不能遗留。 |
| 7 | 根代理安全取回公共proof；`scripts/validation/browser_partial_chain.mjs --proof <固定本地路径>`从固定stage私有凭据文件新登录真实API，运行下面浏览器检查。 | 只读公共proof严格限额；固定`http://127.0.0.1:18080`，无route/mock、trace、HAR、video或原始失败诊断。 |

暂停/恢复和临时管理容器由根代理现有部署协调执行；验证脚本本身不调用Docker或SSH。`prepare/verify/cleanup`状态只放在root0700的本轮私有目录，记录创建对象的OID/指纹与本轮task身份，不放入公开交付树；cleanup只认这一份状态，不搜索或认领未知对象。测试应全部用TemporaryDirectory及fake DB/API，无凭据输出。

## 必需后端断言

- Task仍为COLLECTED/FAILED，job确为failed且无lease，当前job/run/最新采集attempt/RAW id/digest全部一致；`max_attempts=1`从真实领取时起生效。
- 同一task/attempt的真实`ANALYSIS_PARTIAL_SAVED`事件在真实`ANALYSIS_FAILED`之前；它们不是管理脚本写入。不能仅凭事件证明完整解析，必须同时校验真实检查点与输入摘要、计数80+20。
- DB的`analysis_jobs.result`由真实`save_partial`写入，按冻结shared schema校验，最多16KiB；source=import、kind=IMPORT_COLLAPSED、Top函数为固定synthetic符号，不含paths、URL、tool report或额外键。
- 当前attempt没有已发布的RESULT/MANIFEST元数据，Task没有成功结果；已上传但回滚未引用的S3对象明确记为此故障的副作用，不假称不存在。
- 真实授权`GET /tasks/:id/partial-result`为200，其data与PG当前检查点一致；Task详情的`analysis_identity`相同。成功`/result`仍为409；无权限账户仍沿用现有404/授权规则。

## 必需浏览器断言

独立新context、新登录，无成功缓存。375/1440串行访问真实任务页，等待真实partial接口返回并核对公共proof中的当前身份；面板显示“未完成分析 · 已解析摘要”、总权重100和source import，保留失败时间线、失败信息与当前RAW制品。不得出现成功完整火焰图、partial驱动的AI/Compare，也不得为失败任务请求成功`/result`补位。

在两宽度执行axe、键盘展开当前执行标识和无整页横向溢出检查；默认视觉截图先blur/scrollTo(0,0)/等待两帧，再分别保留viewport与fullPage。只输出已核对的image/source/bundle/schema/proof SHA、IDs、计数、状态与截图路径；失败不输出页面正文、原request/response或浏览器原错误。根代理再目检真实stage图，不沿用导入fixture图。

故障移除后可追加一次真实重新分析恢复：先观察新当前版本没有旧partial，随后等待真实SUCCESS并检查新完整结果；不能假定手动reanalyze后的run一定数值递增，应按API当前identity及Task.version核对。此扩展必须独立记录，不能把当前本机mock取消/重试证据重标为真实恢复通过。

## 当前状态

Web rc6实际镜像已构建，公开proof为`web-runtime-image-rc6-2026-10-18.json`，SHA256 `ecabf8ce91496bf3c5a7c25633735e3ebe4751f06c7b66b2551f13f0c3a53584`；其bundle/source仍与rc6冻结一致。API/Analyzer/Ops rc4与Web rc6的实际ID已进入本地stage入口；Core/Agent实际pin尚未交付入口，安全扫描与完整stage交接仍待完成，故入口继续failclosed。本计划没有执行任何真实部分分析故障链。
