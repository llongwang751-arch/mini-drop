# GitHub 发布方式与公开范围

用户于 2026-10-20 明确授权用当前 tx-drop 替换 `llongwang751-arch/mini-drop` 的过时项目文件。发布采用以原 `master` 为父提交的完整源码快照，普通快进推送；原提交历史仍可回退。发布在独立目录准备，原本机开发工作区不会被 reset 或 clean。

公开内容包含四层平台源码、AgentScope runtime、契约、数据库迁移、依赖锁、部署模板、自动化测试、文档、脱敏验收摘要及截图。排除生产 `.env`、SSH/模型密钥、登录态、缓存、数据库、沙箱工作目录、现场原始 profile 和录像 Git blob。`analyzer/tests/fixtures/collected` 是显式标记的 toy 导入样本，有生成器及 SHA/大小 manifest，用于真实格式解析回归，不属于业务采集数据。

CRM 和 Saber 仅提供白名单导出工具、harness、冻结检查及基准。执行 `integrations/crm-python/package.py` 或 `integrations/saber-python/package.py` 时必须明确提供 `--source` 和 `--output`；输出目录须新建或为空且在源码树外。工具不修改原项目、不默认读取桌面目录；部署外部源码前仍须具备对应授权。

完整彩排录像作为 [AgentScope 发布附件](https://github.com/llongwang751-arch/mini-drop/releases/tag/v2026.10.20-agentscope) 单独分发。仓库保留成功和失败的 JSON 收据、截图及视频解码检查；JSON 中的原始录像相对路径是证据来源，不表示录像存放在 Git tree 中。

本次本机实际执行：Web 137 项测试、生产构建及 Prettier；76 HTTP 路由/91 schema、任务契约和 Proto 兼容检查；本地启动器 8 项、增量打包 5 项、外部源码导出 4 项回归。AgentScope Linux33、fresh PG117、真实浏览器闭环及采集器历史检查另见发布证据，不声称本次重新执行。GitHub `quality` 在推送后自动运行，状态以 Actions 为准；`release` 只支持手动触发，不自动部署生产，也不把工作流文件存在当作镜像签名或完整安全门禁已通过。

本次 GitHub 发布只调整 CI、生成的 HTTP 契约说明、文档和本地导出工具，不改变已验收的生产运行时。服务器同步源码与发布绑定信息时保留 `.env`、Secret、数据卷、命名镜像和现有容器。
