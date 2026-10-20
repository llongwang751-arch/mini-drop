# 全量交付与验收矩阵

更新：2026-10-20。用户已将部署目标变更为腾讯云 **[https://82.157.81.79](https://82.157.81.79)**，当前发布 **20261020.3-diagnosis**：API3、Web2、Core/Agent/Analyzer/Ops/网关1，16个运行容器、SQL schema6。继续使用tx-drop，独立开发DeepSeek诊断Agent；企业登录、异地备份展示保持移除。

本页记录新主机实际验收。迁移前完整平台的16类Runner/20场景、百万任务、各格式解析及故障演练保留在[历史矩阵](history/DELIVERY_MATRIX_20261019.5.md)和[旧发布](releases/20261019.5-platform.md)，不作为这些场景已在新主机全部重跑的证明。旧120.24.187.205未能重新连接，当前演示不依赖它。

| 范围 | 当前实现与实际检查 | 边界 |
|---|---|---|
| 基础平台 | C++采集/Go编排/Python分析/React独立；18 TaskKind、65 HTTP路由/82 Schema检查通过，schema6真实迁移和最低版本拒绝测试通过 | 18类型包括导入/复合；不是18个现场采样器 |
| 性能采集 | 腾讯云受控UID10001进程PROC_CPU任务1、PERF_CPU任务2真实采集、解析成功；RAW大小/SHA下载校验通过 | PROC_CPU仅线程tick；PERF_CPU是采样权重；未测任意业务进程 |
| 诊断Agent | 持久化有界工具循环，摘要/热点/质量/调用链/复测五工具；任务2的真实DeepSeek诊断2实际调用全部五工具 | 输出待验证假设；不等于已确认根因、源码行或业务收益 |
| 复测闭环 | 人工独立确认后正常链路创建任务4，实际PERF_CPU采集/分析成功；相同确认键返回同一任务；真实对比页浏览器通过 | 当前同类采集器自定义时长/频率；未实现自然语言从零任意编排 |
| 权限与恢复 | 最新真实PG34项无失败/跳过，含并发准入、幂等、事务回滚、取消、权限撤销、旧attempt/run/输入哈希/租约栅栏、崩溃重领和复测身份保持 | 真实模型使用明确导入fixture另计；测试不证明语义零幻觉 |
| 原生构建 | 新机Release/Werror构建与16 CTest全部通过；固定SHA async-profiler离线构建成功，坏包/超限拒绝2回归通过 | Core/Agent完整工具存在和现场权限是两个门禁；新机未重跑16类Runner全部场景 |
| Web | 133单测、构建、5个诊断浏览器fixture流程通过；公网12页面×1440/375共24检查，axe0/错误0/溢出0，真实报告/对比截图已审阅 | fixture与真实公网证据分别标记，不互相替代 |
| 公网与身份 | Let's Encrypt受信任IP HTTPS、Secure Cookie；本机外网与服务器均验证；新Tencent Agent CN实际90天换叶成功，CA保持 | 六小时续期/每日内部叶检查已启用；未做本机真实重启/CA根轮换 |
| 存储/备份 | 私有MinIO、默认AES256及真实制品SSE检查；最终在线PG/S3备份、隔离PG恢复、对象哈希/元数据/S3往返通过 | 同机备份；没有配置定时异地备份，不恢复其展示 |
| 运维 | 7个Prometheus目标up；Grafana/Tempo真实200；备份/公网续期/内部TLS/监控状态4组service成功且timer active；helper有界2GiB盘启动前挂载依赖已安装 | 未重跑历史四服务span、所有告警规则故障及实际重启演练；单机无物理HA |
| 旧部署清理 | /home/ubuntu/drop源码及DB先归档，8旧容器/旧源码已删除，旧drop_drop-pg卷和命名镜像保留 | 未运行compose down -v；归档在root保护目录，不再提供旧服务 |
| 供应链与容量 | 每个运行镜像ID、基础镜像digest、API二进制SHA及构建来源已记录 | 新镜像未完成统一漏洞扫描/签名发布；旧扫描只适用于旧镜像；长期容量、WAN、全部ARM工具/物理HA未验收 |

证据入口：[部署](evidence/diagnosis-20261020/deployment-proof.json)、[真实采集与模型闭环](evidence/diagnosis-20261020/live-diagnosis.json)、[公网浏览器](evidence/diagnosis-20261020/public/browser-acceptance.json)、[PG](evidence/diagnosis-20261020/postgres-acceptance.json)。设计取舍与一手来源见[公开方案调研](DIAGNOSIS_AGENT_RESEARCH.md)，实际边界见[剩余验收](CAPABILITY_GAPS.md)。
