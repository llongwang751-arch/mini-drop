# 云端验收数据清理与重启复验（2026-09-24）

用户授权删除云端无用文档和缓存。本次仅处理办公助手中 14 篇标题为 `upload.txt` 或 `million-browser-acceptance.txt`、来源为 `user_upload` 的旧验收文档，以及 Docker 未使用的构建缓存。清理前办公助手有 14 篇文档、5,380,129 字、26,026 个 SQLite RAG 分块；其中 5 篇约百万字。全部 14 篇先经原应用 `DELETE /api/documents/{id}` 成功删除，页面列表变为 0、检索分块变为 0。

原删除接口对文档版本采用软删除，版本正文仍占 SQLite，且 129,088 条历史投影任务中大量 Elasticsearch 任务在当前无 Elasticsearch 的环境里待处理。维护窗口内确认全部 14 篇均为已删除的验收样本、无其他文档和检索块后，停止 `agi-office-backend.service`，清除旧 `rag_chunks` 向量集合、这 14 篇的版本和文档行及其失效投影任务，再运行 SQLite `VACUUM` 和 `integrity_check`（结果 `ok`）。原数据库文件、向量库目录、平台 PostgreSQL/MinIO 卷、项目源码、发布和回滚版本、历史诊断证据均保留；没有把旧百万字请求的计时记录伪称为可继续检索的文档。

清理结果：`application.db` 从 1,671,077,888 字节降至 12,308,480 字节；`docker builder prune -f` 回收 5.848 GB；根盘从约 1.2 GiB 可用、97% 使用变为约 8.3 GiB 可用、78% 使用。未清理旧发布和 Docker 镜像，因为它们仍服务于回滚；其他 `/tmp` 阶段材料和平台证据也未凭目录名推定为无用。

为验证项目仍可演示，重启后新建 `doc_3524a665221efaf9` 小型向量健康样本。实际入库 1 块，SQLite Embedding 为 1024 维；原问答接口对样本问题返回 `retrieval_mode=semantic`、1 个候选且回答包含预期编号。再次重启后，文档列表 1 篇、版本 1 条、RAG 分块 1 条，语义召回和回答仍通过；请求快照仍为 `mini-drop.office-observations.v2`。办公助手服务活跃，`MemoryMax=1.5 GiB`、`NRestarts=0`、新 cgroup `oom=0`，平台 `/api/healthz` 的三项依赖健康。旧百万字样本只剩无正文的历史验收报告和近 24 小时请求计时，后续完整演示须重新上传。
