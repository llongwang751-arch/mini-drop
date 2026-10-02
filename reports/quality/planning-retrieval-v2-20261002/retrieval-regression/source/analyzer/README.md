# Python 离线分析模块

本目录保存独立 Analyzer 包和 FlameGraph 工具。生产 Compose 中的后台租约、重试、状态回写及 AI 证据准入由 `server/app/analysis_jobs.py` 等 Worker 入口承载。

分析输入输出契约见 `docs/contracts/collector-evidence.md`。
