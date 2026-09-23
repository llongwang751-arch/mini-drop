# AGI-saber 百万字入库与 Mini-Drop 全链路验收（2026-09-23）

## 结果口径

原办公助手已从“无可用 Embedding”接入硅基流动 `BAAI/bge-m3` 与持久化 Milvus Lite。API Key 只在云端 root 私有环境文件，报告不含凭据或文档正文。100 万字真实接口上传 HTTP 200、7,701 块、241/241 次 Embedding 成功；重启后 `retrieval_mode=semantic`、非零候选，回答包含测试文档的预期事实。完整浏览器重新上传和 Mini-Drop 同请求页面复验也已通过，与直接 API 验收分别记录。

首轮真实浏览器百万字上传收到 **HTTP 504**，页面显示“上传失败”；后台随后仍完成 7,701/7,701 向量入库。原因是 `/api/office/` Nginx 读取超时 120 秒，小于远程 Embedding 所需约 5 分钟。已将该路由专属超时改为 600 秒，再从同一网页上传成功，HTTP 200、页面“上传完成（7701 块）”、浏览器错误 0。首次 504 的验证器输出誊录保存在 `output/cloud-long-ingest-20260923T144800Z/browser-vector/first-504-observation.json`，标明不是原始网络抓包；验证器复用了截图文件名，因此失败截图已被成功重跑覆盖，不将当前截图声称为失败证据。

重跑网页请求 `ef8f28a846b14243a35e19bd06ca247c` 的后台观测：正文 999,999 字（网页测试文本末尾规范化后）、7,701 分块和 7,701 个已入库向量，241/241 次 Embedding 成功；总耗时 268.79 秒，向量化 226.94 秒、索引 41.52 秒、其中向量库写入 2.97 秒；进程 CPU 47.16 秒、RSS 峰值 307.8 MiB，服务组内存峰值 946.1 MiB、限额 1,024 MiB，无 OOM/重启。Mini-Drop 公网页面选择**同一个 request_id** 后显示这些数据与“主要耗时：向量化”。关联体检会话 `insight_4cbf147d00a041db925736bc8143532b` 有 1 次工具、2 条 Evidence、1 份 Report；当前窗口进程 CPU 2.4%、RSS 553 MiB，报告为 `PARTIAL_WITHOUT_COUNTER`，页面明确“尚未定位根因”，没有把业务慢阶段冒充 VERIFIED。页面截图和自动核对在 `output/cloud-long-ingest-20260923T144800Z/browser-vector/mini-drop-*`。向刚上传文档提问，语义召回有 10 个候选、回答正确包含“申请人、审批日期、归档编号”。

三段故障演练首次在语义检索模式采到注入标记 0/2500/0 ms，检索 1063.6/3407.7/1448.3 ms；旧页面要求故障与恢复差超过 2000 ms，实际差约 1959 ms，因此错误显示未确认恢复。新判据仍要求同 PID/版本、三次完成和注入标记，并接受两侧各至少 1500 ms 的差值；对应单测锁定这组实测波动。最终网页复测 **`restored=true`**、三个阶段齐全、浏览器错误 0；检索分别为 **1457.0 → 3517.5 → 1204.2 ms**，注入标记为 **0 → 2500 → 0 ms**，同 PID 3666614、同版本。网页输出在 `output/cloud-sre-exercise-20260923T141511Z/browser/exercise.json`；故障相关 AI 诊断单独继续循证，不把此受控差值写成自然故障根因 VERIFIED。

重启暴露了请求列表的持久化缺口：旧 `requests.json` 由进程内空队列重新生成，新问答会覆盖旧的百万字上传记录，尽管向量数据和已生成体检报告仍在。已升级为 `mini-drop.office-observations.v2`：启动时只继承同服务、字段白名单、文件不超过 256 KiB、近 24 小时的旧记录；Mini-Drop 读取旧 v1 与新 v2，并核对前后 PID 的时间边界，不把旧 PID 当作当前可采样目标。发布前从两份保留的真实快照无冲突合并 12 条，保留原始部署前备份；发布后新问答使记录达 13 条，再次干净重启与语义问答后达 **14 条、4 个历史 PID**。公网服务状态 `OBSERVED`、请求列表 `AVAILABLE`、`invalid_records=0`，原百万字 request_id 仍在。第二次重启后 `MemoryMax=1.5 GiB`、峰值 1,299,030,016 字节、`oom=0`、`NRestarts=0`，语义问答继续答对。
最终 Web 增加请求下拉框搜索。历史上传排在虚拟列表后部时，在原页面输入 `999,999` 即可筛出；公网选中旧 request_id、点击“检查当前状态”后，体检重新绑定当前 PID 3803042、显示当前进程指标与历史上传慢阶段，报告为 `INSUFFICIENT_EVIDENCE`、浏览器错误 0。截图与 `mini-drop-ui.json` 位于 `output/cloud-long-ingest-20260923T144800Z/browser-vector/`。旧请求展示的 1,024 MiB 限额是当时测量，当前 systemd 限额为 1.5 GiB，两者不矛盾。

| 观测 | 真实结果 | 证据 |
| --- | --- | --- |
| 旧词法模式 10 万字 | 770 块，6.26 秒，索引 6.22 秒 | `output/cloud-long-ingest-20260923T144300Z/baseline-100k.json` |
| SQLite 100 块/事务后 10 万字 | 770 块，1.34 秒，索引 1.28 秒 | `output/cloud-long-ingest-20260923T144800Z/after-100k.json` |
| 向量模式 100 万字 | 7,701 块，289.09 秒，向量化 230.84 秒，索引 58.01 秒，241 次 Embedding 均成功 | `output/cloud-long-ingest-20260923T144800Z/million-vector.json` |
| 新埋点 2 万字 | 153 块、153 已入库向量；Embedding 5/5 成功；7.21 秒，向量化 5.97 秒；进程 RSS 峰值 287.5 MiB、服务组内存峰值 308.8 MiB、限额 768 MiB | `output/cloud-long-ingest-20260923T144800Z/cgroup-smoke-20k.json` |
| 重启后语义问答 | `retrieval_mode=semantic`、候选非零、回答包含测试事实 | `output/cloud-long-ingest-20260923T144800Z/verify_vector_query.py` 的实机输出 |

10 万字的前后对比是同一文本、旧词法模式的一次样本，用于定位 SQLite 提交热点；不能声称平均加速比或把它与有远程 Embedding 的 100 万字结果直接比较。百万字期间进程 RSS 峰值 320.3 MiB，systemd 服务组（含向量库运行时）实测峰值约 647.6 MiB，越过旧 512 MiB 服务限额；先升至 768 MiB 后，网页百万字复验触发 `memory.events:max`，升至 1 GiB 后网页上传成功。随后重启加载多篇百万字文档时，1 GiB 限额导致一次 OOM 自动重启和短暂接口不可用。最终将限额持久调至 **1.5 GiB**，干净重启与新文档问答通过，服务组峰值 1,335,877,632 字节（约 1.24 GiB），新 cgroup 的 `oom=0`、`NRestarts=0`，无 swap 使用。这解释了“当前机器内存不足”的具体含义：此前是**办公助手服务的 cgroup 限额不足**，不是整台主机的可用内存归零。新埋点把进程和服务组分别展示，防止误读；重启验收状态原始文本在 `output/cloud-long-ingest-20260923T144800Z/restart-memory-1536.txt`。

平台主视图根据实际计时显示最大慢阶段。新的百万字样本向量化约占 80%，所以预期为“已定位慢阶段：向量化”；此判断只说明请求在哪里耗时，尚不能区分供应商排队、网络、批量配置或模型本身。系统指标的进程采集是后续复现窗口，历史请求数据仍独立显示。根因 Evidence Gate 不因演示而放宽为假 VERIFIED。

## 发布与回滚

办公助手最终发布 `/opt/agi-office/releases/20260923T162200Z`，独立于平台发布；最终平台 `/opt/mini-drop-releases/20260923T163300Z`，Diagnosis Worker 为 `20260923T162100Z`，Web 为 `20260923T163300Z`。向量库 `/var/lib/agi-office/vector/milvus.db`、SQLite 用户/文档库、平台数据库和对象存储不在发布目录内。发布指针都是符号链接，旧版本保留。回滚平台需使用最终发布 `private/rollback.compose.json` 恢复旧 Web 镜像，再切回旧平台链接；若连 Worker 也回滚，应依其独立发布 `private/rollback.compose.json` 恢复上一个 Worker。回滚办公助手需先确认旧版是否能读取当前数据库与向量数据，不删除或重建数据目录。旧应用版本不会保留 v2 的跨重启历史请求，因此回滚时应保留 v2 快照与部署前备份。必要时在配置中禁用向量模式作为应急降级，并在页面标出词法模式。

复现操作见 [全链路验收步骤](../../docs/FULL_CHAIN_ACCEPTANCE.md)。21 个故障广场场景的控制链路与严格根因验收分开：21/21 启停并不等于根因 21/21，通过严格门禁的仍只有 1/21。

最终代码检查：Python 全量 **625 passed、7 skipped**；Web 全量 **203 passed**、生产构建通过；Nginx 配置语法通过；公网 `/api/healthz` 的 Control Plane、数据库、诊断 AI 均为 healthy。故障广场桌面四类卡片、手机无横向溢出、浏览器错误 0。AGI 原源码两份补丁在保存的前置源码上通过 `git apply --check`。这些检查不替代长期负载、并发上传或生产容量验证。

容量边界：云主机只有一块 40 GiB 根盘，网页复验后剩余约 1.2 GiB（97% 使用），办公助手 SQLite 约 1.6 GiB、Milvus Lite 约 111 MiB。此次没有删除旧发布、对象库或用户文档，也没有清空数据库。当前样本可演示，但不能承诺无限次百万字上传；继续高频演示前需扩容根盘或制定有审核的文档保留/归档策略。
