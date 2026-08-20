# Python 兼容控制面、分析与 AI

本目录承担尚未迁移的 FastAPI/gRPC 接口、SQLAlchemy 数据模型、状态机、Analyzer Worker、自然语言解析和循证 AI 诊断。

Go API 与 C++ 控制面按契约逐项替换这些职责。迁移过程中 Python 服务继续提供兼容路由和回滚路径，不应作为无用旧代码删除。
