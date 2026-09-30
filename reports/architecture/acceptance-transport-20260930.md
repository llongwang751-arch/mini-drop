# 诊断验收的传输路径与采样时间线（2026-09-30）

上轮两个云端GC批次因公开接口TLS/读取错误而STOPPED_UNSAFE_TO_CONTINUE。独立安全检查已确认全部故障停止、会话和工具终态。本轮先拆分客户端、代理、API和后台采样的证据，不增加自动重试，不延长验收预算，也不改写旧批次。

## 定位证据

本机urllib读取系统代理，HTTP/HTTPS均经过本机127.0.0.1:7897。在同一公开诊断读取接口交替测量四组代理/直连路径，代理路径一次12秒超时、三次200；直连四次200（约74至444ms）。随后使用显式直连，健康、诊断与工具列表三个接口共30次读取全部完成，最慢81.26ms；私有CA链和主机名校验保持启用。该测量定位了可复现的代理路径超时，但旧请求缺少客户端阶段/路由记录，不能断言每个历史TLS错误都有相同根因。

原失败时段的API日志中74条非SSE GET均200，服务端耗时最大34ms；容器没有重启，未观察到读取请求的数据库/Worker长阻塞。规划请求另有模型OpenAITimeoutError，首批一个规划POST耗时47.868秒。这是不同路径的延迟，不能与GET超时混为一谈。

重试批次在10:20:57—10:21:02 UTC已测得撤销恢复。后台JVM alloc工具创建于10:21:32，perf工具创建于10:22:19，均晚于撤销窗口。JVM同窗计数器因此记录零GC活动、Profile样本不足；随后perf返回NO_PERF_SAMPLES。工具失败没有被当作反证或有效支持。这条时间线解释了采样时故障已撤销，不能把它当作JVM工具没有执行，也不需要降低样本门槛来补成绩。

## 客户端改动与回归

`scripts/verify_interview_demo.py::Client`增加显式`proxy_mode=direct`；命令入口为`--proxy-mode direct`。默认仍继承环境代理，不修改系统代理设置。直连使用独立ProxyHandler和调用时当前TLS上下文，适配调用者安装私有可信CA；JSON和原始产物走同一路由。

TimeoutError、SSLEOFError与HTTP连接关闭现在转换为含方法、路径和异常类型的AcceptanceError，便于判断哪条请求失败。没有换路径重试，也不在不确定的POST结果之后重发创建/注入请求；已有清理和失败判定保留。

9项新用例在旧代码全部失败；修复后相关96项通过。测试覆盖真实本地HTTP服务的JSON/产物读取、显式绕过代理、当前TLS上下文与证书校验、非法模式，以及GET/POST超时/EOF/断连时只有一次请求且不暴露凭据。完整Python质量门禁1201通过/8项已登记依赖跳过、关键覆盖率无违约，原始负向/修复日志和JUnit另存。

## 冻结部署上的独立复验

本轮修改仅为本地验收客户端；云端后端保持20260930T100034Z/d26bc2c，Web保持20260930T084451Z/c9b9964，没有新建运行时发布或修改数据库。新入口`run_direct_strict.py`显式选择直连，在campaign中登记客户端和入口源码SHA、TLS校验及原部署来源。注入300秒、诊断240秒、三轮要求、样本与因果根因评分均保持不变。新批次使用独立输出，不覆盖旧失败，也不更新完整21场景的latest_acceptance。

新GC批次`fault-plaza-java-direct-deployed-20260930.json`已COMPLETED，无传输/记录读取错误。取证链、真实注入、撤销恢复、清理与会话收束均通过；三个工具（系统、内存、JVM）全部COMPLETED，会话COMPLETED。报告为VERIFIED/BOUNDED_OBSERVATION/causal=false：2762个有效样本、分配热点99.9%、GC105次/440ms。严格因果根因0/1，整项仍false；执行器exit1符合这项严格判定，不表示传输再次失败。

新campaign SHA为`3e1d60e91cf914b0214c57e6aaa387a501dc286df7dfa7486683d4033727a235`，case SHA为`3acfed65ece45218a652d549b8de648cc03b95f37f69a0f405b514c14db40982`。7个原始产物逐个下载并核对大小和SHA，浏览器实拍显示已验证观测、根因仍待确认，无异常。原始日志、JUnit、覆盖率、传输测量和新云端证据见`reports/quality/rpc-read-stability-20260930/`。归档时误以artifact数据库ID访问download返回404，空ZIP与错误记录保留；正确使用artifact_type后7个文件摘要全部一致，可信归档为cloud-verified-artifacts.zip。

[CI36704929921](https://github.com/llongwang751-arch/mini-drop/actions/runs/36704929921)成功13/13；测试源码67b428828f4b6b2d93d3917e81b5c18ddd463de4，测试merge8a4f9c3fabf59840c373c86447890af9a6a311c0。Python1201通过/8登记跳过，真实PG专项6通过零跳过，4份CI原始ZIP归档并核对SHA。CI和后续文档提交不改变冻结部署源码；本轮不需要重新部署Worker/Web，因为修改的是本地验收客户端。保留原失败记录、故障清理及旧运行时回滚配置。
