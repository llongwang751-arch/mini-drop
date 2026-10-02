# AGI-saber 云端原应用补丁

这两份补丁只针对 2026-09-23 原办公助手云端发布中的 `internal/` 源码，不能把 Mini-Drop 仓库自身当作目标根目录。补丁在目标源码副本上通过 `git apply --check`。应用前核对下表 SHA-256；不一致应重新审查差异，不能使用 `--reject` 强行套用。

| 原应用文件 | 补丁前 SHA-256 |
| --- | --- |
| `internal/application/local_repos.py` | `ce138f2c15d1a16ca846bc785d6f725fcb36020e5516eacfc7f37b89d01dc7a2` |
| `internal/rag/hybrid.py` | `fc7de07d4fdb2c3f7f269d79b151075c26abe29d3d92f000e91bc751ed0ca003` |
| `internal/rag/rag.py` | `d7a6354a4b323ec9857b2453a9098d3ccac5e707cf31b65782f74978c9d8d158` |
| `internal/infra/infra.py` | `d7fb58441683dcaf56955637680a24b57e2f89a92dc5fc55384ab5276c45cbac` |

先运行 `git apply --check long-ingest-batch.patch` 和 `git apply --check milvus-lite.patch`，核对后分别应用。`long-ingest-batch.patch` 包含 SQLite 有界批量提交、流式 Embedding/向量写入与兼容旧的无向量 outbox；`milvus-lite.patch` 在固定私有路径创建、重载和查询向量 collection。办公助手虚拟环境需安装相邻目录的 `requirements-vector.txt`。凭据由宿主私有 `/etc/agi-office/backend.env` 配置，至少包括 `AGI_EMBEDDING_API_URL`、`AGI_EMBEDDING_API_KEY`、`AGI_EMBEDDING_MODEL`、`AGI_EMBEDDING_BATCH_SIZE`、`AGI_MILVUS_LITE_PATH`；任何配置例子都不要填真实 Key。密钥不应进入补丁、系统日志、观测快照或网页。

补丁后的办公助手仍使用原网页、原用户库和原 API。Mini-Drop 只读请求观测快照并另外绑定当前进程，业务数据库与向量库不挂进 Mini-Drop 容器。新应用版本上线应先在隔离数据副本验证迁移、完整上传、重启后语义检索，再滚动发布，不要覆盖线上数据目录。
