# Grafana Pyroscope 2.2.1 成熟产品实测

在云控制节点以 0.75 CPU / 384 MiB 限额运行官方镜像，使用官方 Python SDK 上报 20 秒 `CPU_HOTSPOT` 工作负载。服务返回真实 profile series，聚合 flamegraph 包含 `order_compute` 热点函数。三次查询中位延迟为 4.24 ms。

这条对照只评价持续剖析的采集、窗口查询和函数下钻，不把 Pyroscope 当作 AI 根因诊断系统，也不生成跨赛道总分。原始查询、series、flamegraph、工作负载均保留在本目录。
