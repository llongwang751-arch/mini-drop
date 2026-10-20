# 本次执行的 partial 分析结果：最小闭环设计

状态：2026-10-18只读设计；尚未修改API、Analyzer、Web冻结运行文件，未执行远端操作。指南§8.6第1668行要求“可查看的 partial result”。当前RAW/LOG/BINARY文件面板是已保存采集文件，不能替代下面的部分分析能力。

## 结论与持久化选择

推荐复用`analysis_jobs.result`存一个有专用marker的有界检查点；`/result`继续只提供成功分析，新增`GET /api/v1/tasks/:id/partial-result`只提供当前失败分析run的快照。任务状态继续FAILED，不把部分结果当成功，不回退至历史结果。这个最小方案**不需要新SQL migration或schema 6**，也不改变C++/Agent protobuf。

直接依据：

- `db/migrations/001_initial.sql:48`的`analysis_jobs`已有`result jsonb`、`attempt`、`lease_owner`、`lease_expires_at`、`status`和`UNIQUE(task_id,attempt_id)`。所有需要持久化的标识可放在既有JSONB中，状态仍使用已有running/retry/failed/success。
- `db/migrations/002_platform.sql:80`已有`analysis_job_runs(job_id,task_id,attempt_id,run_number,status,result,...)`和`UNIQUE(job_id,run_number)`。`api/internal/service/administration.go:75`已经归档旧run，随后清空当前result再重分析。
- `tasks`没有数据库`result`列。`api/internal/httpapi/resources.go:162`中的Task.result是从成功job计算的HTTP响应字段，无需改tasks表。
- `analyzer/drop_analyzer/hprof.py:187`的checkpoint是开发机/容器私有SQLite索引的解析恢复位置。它不是验证完的公开分析结果，不读取/公开该文件或它的路径。
- 现有Artifact支持`INTERMEDIATE`和JSONB metadata，但必须对应真实对象；其三天保留期由`002_platform.sql:41`的trigger决定。当前Analyzer只在`Repository.complete`中将上传对象、结果和SUCCESS一起提交。复用它发布partial需要新增S3写入和独立发布事务，且恰好在对象存储故障时可能失效。因此第一版使用数据库小快照，不伪造INTERMEDIATE对象/下载入口，也不宣称该inline JSON自动享有INTERMEDIATE三天删除策略。它沿用既有job/result生命周期及归档规则；若产品另要求三天物理清理，应单独明确并接入维护任务，而不是隐含承诺。

## 公共契约 v1

新增接口沿用现有Task read授权、JSON envelope和错误约定。没有可用当前partial时返回409 `PARTIAL_RESULT_NOT_READY`；越权/不存在保持现有404行为。不改变成功`/result`、AI和Compare的数据来源。

```json
{
  "schema_version": 1,
  "completeness": "partial",
  "task_id": "7",
  "attempt_id": "20",
  "analysis_job_id": "30",
  "analysis_run": 3,
  "input_artifact_id": "40",
  "input_digest": "64 lowercase hex characters",
  "kind": "IMPORT_COLLAPSED",
  "completed_stages": ["input_verified", "parsed"],
  "missing_stages": ["diagnostic_rules", "result_artifacts", "final_commit"],
  "summary": {
    "source": "import",
    "unit": "samples",
    "total_samples": 150,
    "unique_stacks": 2,
    "node_count": 4,
    "max_depth": 3
  },
  "top_functions": [
    {
      "symbol_sha256": "64 lowercase hex characters",
      "self": 100,
      "total": 150,
      "self_percent": 66.6667,
      "total_percent": 100
    }
  ],
  "sections": {
    "io": {"operations": 5, "bytes": 1024, "error_operations": 1}
  }
}
```

这是结构示意，ID/摘要均为显式导入示例，两个SHA占位字符串不是合法可提交payload。实际代码应由同一组契约fixture约束Python、Go和TypeScript，拒绝未知键。

约束：

- `task_id/attempt_id/analysis_job_id/input_artifact_id`为正的int64十进制字符串；`analysis_run`为正SQL integer；所有标识由受控job/DB生成，不从profile提供的metadata取值。
- `completeness`必须精确为`partial`，`schema_version`精确1，`input_digest`和`symbol_sha256`为小写64位hex。Top K最多20条；符号只暴露SHA256，界面显示“符号 #摘要”。不复制函数原名中的源文件路径，不伪装这些哈希是原始函数名。
- `summary`只取验证完成的parser计数。所有数字为有限值、非负且不超过JS安全整数范围；整数计数必须为整数，百分比在0..100。缺少的可选值省略，不把未知变成0。`source/unit/kind`使用显式已知枚举，未知单位显示unknown，不回显任意输入字符串。
- 只允许`summary`、Top K和下面列出的固定数值sections；不复制完整flamegraph、collector/raw metadata、tool report、maps、PID/地址、命令、对象key/URL、路径、建议文本、HTML或任意字符串字典。
- 总UTF-8编码最多64KiB、结构深度最多6、每个section最多16个标量字段。Python使用`allow_nan=False`，Go独立严格decode和数值/枚举检查，Web再次校验。失败校验不能回退到旧result。
- `completed_stages`与`missing_stages`来自固定枚举，不使用profile文字。missing指**本检查点未包含的成果**，不推断后续阶段从未执行：例如上传失败前规则可能已经算过，但这个parse检查点不包含规则成果。

建议第一版sections只覆盖现有parser的这些计数，其他字段不透传：

| Section | 允许的原始数值来源；返回时扁平化并白名单命名 |
|---|---|
| heap | `totals.objects/shallow_bytes/root_count/reachable_objects/unreachable_objects/sample_weight` |
| io | `operations/bytes/error_operations/read_operations/write_operations/latency_observations/lost_events` |
| memory | `allocated_bytes/freed_bytes/live_bytes/peak_live_bytes/allocations/frees/unknown_frees/failed_allocations/lost_events` |
| resources | `sample_count`和`peaks`中的`cpu_percent/rss_bytes/vms_bytes/threads/fd_count` |
| binary | `totals.branches/mispredictions` |
| script | `exit_code/duration_ms`；不含原工具output/template动态文本 |

这些字段对应`events.py`、`hprof.py`、`pprof.py`、`native.py`、`external.py`的现有结构。不必为没有section的CPU任务生成虚构section；summary和Top K已经是有效的部分分析。

## 写入和失败语义

1. `Repository.claim`每次取得新run时，在同一事务内递增`attempt`、设置owner/lease并`result=NULL`。包括过期租约被新worker接管，避免旧run的检查点继续存在。
2. `worker.process_job`完成RAW/附件SHA校验、`analyze_isolated`成功返回后，使用独立纯函数从validated parsed构造上述有界快照。放在读取规则/`make_result`/`build_outputs`/对象上传之前；此时不要求S3可写。
3. `Repository.checkpoint_partial(job, snapshot, input_artifact)`沿用`_lock_owned`的锁顺序：锁当前Task，再锁当前job。必须同时确认最新采集attempt、Task COLLECTED/RUNNING、job running、当前owner、当前run和未过期lease；再核对DB最新RAW的id/hash与已校验输入匹配。
4. 更新`analysis_jobs.result`仍带owner/run/`lease_expires_at>clock_timestamp()`条件，rowcount必须1。若发`ANALYSIS_PARTIAL_SAVED`事件，只发job/run/input_digest和计数，不发快照正文、hash前符号或lease owner。事件与checkpoint同事务。像现有`progress`一样在慢触发器/事件之后再次检查lease，超时则整个事务回滚。
5. 后续规则、输出或上传失败时，现有`Repository.fail`保留已提交的**当前run**检查点，状态依旧retry或failed。retry期间接口不展示；再次claim清空。最终run崩溃后，现有过期租约终结流程可保留该run已验证检查点，但不能临时重建或接纳过期worker的新写入。
6. 成功时现有`Repository.complete`原子写入完整成功结果覆盖partial，发布真实RESULT/MANIFEST、建议和SUCCESS。接口不再返回partial。手动reanalyze沿用现有归档及清空；API从不回查`analysis_job_runs`作为当前partial的fallback。
7. lease丢失、取消、新采集attempt、损坏/超限输入或parser未正常返回时，没有合法检查点；保持明确的“尚无可查看部分分析结果”。不从中途stdout、残损response、临时索引、原RAW或旧RESULT拼一个partial。

## API当前性和Web呈现

API使用单条查询将Task、其最新采集attempt、当前failed job、其JSON快照和最新RAW元数据关联：Task须COLLECTED/FAILED且未删除，job.status=failed，JSON的task/attempt/job/run必须分别等于对应DB值，输入artifact id/hash匹配该attempt的最新RAW，marker/schema/体积正确。不能仅按task_id查任意旧failed行。查询后独立严格校验JSON，拒绝无效legacy/伪造记录。查询同样返回当前身份锚点供响应构造，不允许状态检查和任意旧result读取分离。

Web只在Task COLLECTED且analysis FAILED时查询新接口，query key至少含task id与Task.version，切换任务、重试、重新分析时Abort/失效缓存。payload task/attempt必须与当前Task最新attempt吻合；job/run由API的上述单条当前性查询保证。查询409时保留现有无部分结果说明，403/404不重试，不请求成功`/result`作替代。

界面新增“未完成分析 · 已解析摘要”，显示完成阶段/未包含成果、原始来源、计数、Top K哈希符号及可选section计数。保留失败阶段、追踪ID、建议和当前RAW文件面板。页头/时间线依然失败；不显示成功勾选，不开放基于partial的AI/Compare或完整火焰图，不把partial赋给`AnalysisResult`。整个视图为只读展示；重分析仍走现有管理员/CSRF接口。

## 变更面与验证矩阵

| 模块 | 建议文件 | 必需回归 |
|---|---|---|
| 公共契约 | `docs/CONTRACT.md`；新增专用JSON契约/跨语言有效与无效fixtures | marker、标识、数字/枚举、64KiB、深度、额外字段及路径/原报告禁入规则一致 |
| Analyzer | 新`partial.py`；`worker.py`、`repository.py`；对应unit/PG integration | 完整解析后规则/上传失败仍保留本run快照；parser失败无快照；新claim清空；旧owner/run/attempt写入拒绝；慢trigger跨lease回滚；最终过期回收只保留合法本run；成功覆盖；输入id/hash变化拒绝 |
| API | 新domain/严格validator及repository查询文件；`httpapi/server.go`、`resources.go`及authorization/PG tests | owner/share/viewer可读；跨组404；仅FAILED；旧attempt/job/run/digest均拒绝；大体积/未知字段/NaN/私有文本拒绝；成功/result原样；重新分析清空不fallback；不会外发partial到AI |
| Web | `types.ts`；新`partial-analysis.ts`和只读组件；`views/TaskDetail.tsx`；相应unit/e2e fixture | 当前failed才查询；Abort/缓存失效；409/403/404行为；标识错误或未知payload不渲染；未完成标识/来源/阶段/Top K；无成功态/AI/Compare误用；375/1440 axe、键盘、横溢和视觉重验 |
| 联调 | 独立PG/对象存储/实际API/浏览器的故障场景 | 生产者真的先提交检查点，再因限定的后阶段故障FAILED，API返回该精确run，浏览器展示并保留失败；不能只seed一条SQL就宣称整个故障链路已验证 |

成本主要是三个运行模块和三份新镜像/冻结产物，约10–15个运行/测试文件外加契约fixtures，不增加Core镜像、数据库迁移、长期凭据或额外S3对象。每个run增加一次最多64KiB的DB更新及可选事件，正常完整分析结果格式保持兼容。预计新增约25–35个有意义的跨语言边界/生命周期断言；确切测试数以实际运行报告为准。CPU/内存和真实stage故障注入尚未测，不预先给耗时或SLA结论。

当前schema 5的Core验证可继续。API/Web/Analyzer需要先协调此公共结构，再实现和分别验证；旧rc3/rc5候选与既有证据保留，不能把新功能重标为旧冻结产物的一部分。

## 实施契约修订与当前证据

以上为实现前提案，最终跨语言v1收敛为[`contracts/partial-result.schema.json`](../../../contracts/partial-result.schema.json)：数据最大16KiB，Top函数最多20项，名称保留任务授权用户可见的符号并限160个UTF-8字节，另附原符号SHA256；不包含sections、私有路径、原工具报告或完整火焰树。Task详情增加当前`analysis_identity`，Web同时校验任务版本、最新采集尝试、job/run与输入RAW的id/digest。标准HTTP envelope可在数据限额外增加最多1024字节；具体权威说明见`docs/CONTRACT.md`。本设计前文的64KiB、hash-only符号、可选sections和方法命名不代表最终运行契约。

共享schema SHA256为`22ebbd52a3fedc7ffdd466523104697de0dc191cc81869ff9d1ed3ff66d9f942`，显式导入fixture SHA256为`bbf2c3113d5ed0672a02d1cf71d592ef76e84a497e3ee6a85bac216fea3f8139`。Web rc6本机实际131单测、28统一浏览器回归、1项默认视觉补充检查及7项下载安全测试通过，详见`web-rc6-source-freeze.json`和`docs/UI_REVIEW.md`。这些证据不代替真实stage失败链路；API与Analyzer各自的真实数据库验证和候选冻结由对应模块报告记录，不在本提案中重标为全栈完成。
