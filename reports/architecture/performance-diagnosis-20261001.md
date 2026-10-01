# 性能诊断主线修复与交付

已发布Worker/Analyzer/Web：20261001T075239Z，源码10002e94cf8aa46a7e4778385138c35ddfc20d7d。Worker/Analyzer各189份文件、Web56份文件SHA一致，其他10容器保持，Office PID484342且无重启，API二进制保持。13容器运行及健康检查通过，API3依赖健康，21故障inactive；历史因果0/21保持。

恢复21类性能实验主入口并显示全部，工程4项回归单独保留。新增正常/不足/异常/观测验证/因果验证五种范围；正常要求身份、采样、有效CPU/RSS和schema/Analyzer/目标/时间/非降级校验。13信号域使用精确数值判据；关键词不覆盖未采集条件，缺值不填0。修复计算活动冒充CPU热点、保留量冒充增长、累计HTTP均值冒充窗口耗时。新运行时Profile与性能数值报告为有界观测，不以VERIFIED直接冒充因果。

旧21只读复盘：7项通过旧链路检查、9项因终态不足被拒绝、2项因支持不足被拒绝、3项误选目标。v3将链路与完整诊断执行合同分开记录，严格根因门槛不变。初版归档链内部一致17/21，真实健康证据揭示ACCEPT_NEUTRAL漏算，修正为18/21；初版材料保留。这是归档内部一致性，未重新下载旧21原始对象，不是18根因通过。21类还缺固定输入/负载下的针对原因干预与必要采集，详见[性能诊断](../../docs/PERFORMANCE_DIAGNOSIS.md)。

真实Office健康会话insight_731431c05e5a44588cba6c4204022d54完成1个系统采集，原始2下载SHA验证；15样本/14.005秒、CPU0.428%、RSS217.949MiB且增量0。首次真实页面因有效中性证据被忽略而失败，冻结51228f31对同输入返回INSUFFICIENT_OBSERVABILITY，修复后NORMAL_OBSERVED。发布后真实Chrome显示“本次检查正常（已检查范围）”与“检查结果：正常”；会话未证明根因仍保留INSUFFICIENT_EVIDENCE。未检查目标I/O操作耗时、TCP丢包/重传、内存限额及全部业务接口。

精确源码[CI36832916434](https://github.com/llongwang751-arch/mini-drop/actions/runs/36832916434)成功13/13，测试merge d7591cdd65988c0e51d363c32678c3c374b9f93c。Python1299通过/10登记跳过，真实PG8通过零跳过，Web264通过，Chroma独立专项通过；bundle门禁通过。真实Chrome验证21默认性能卡、4工程卡及9份下载SHA、四种宽度无水平溢出，无JS/HTTP异常。三份最终CI原始ZIP及SHA已归档。

负向复现冻结aafd85be两个原始源码模块，7个行为反例全部失败，修复后相同7项全通过。全量首跑旧标题断言失败、第二次覆盖率未达100%、初次真实正常页面失败均保留；未降低测试或根因门槛。本地10跳过与独立PG/Chroma专项分开记录。一小时采用用户已接受成绩，不重跑。

证据在[本轮目录](../quality/performance-diagnosis-20261001/)，最终部署、浏览器、CI ZIP及SHA清单位于[部署证据](../quality/performance-diagnosis-20261001/deployed/evidence-manifest.json)。没有宣称新21类因果成绩通过。剩余重点是目标I/O归属、真实TCP传输观测、多窗口内存行为，以及固定业务输入/负载下的原因干预。
