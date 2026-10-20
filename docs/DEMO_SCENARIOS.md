# 多场景实时演示验收 · 2026-10-20

当前[入口](https://82.157.81.79)：API10、Web10、Agent/Helper3、SQL8。最终三角色策略下16类Runner、20个新任务62–81全部通过。目标为明确标记的合成负载，执行真实采样，不导入profile冒充现场。

## 最终组合版本新任务

| 场景/选项 | 新任务 | 验收 |
|---|---|---|
| PYTHON_MEMORY native=False | [62](https://82.157.81.79/tasks/62) | 真实采集/分析、RAW大小及SHA通过 |
| MEMORY_TRACE category=alloc, max_events=200, stack_depth=8 | [63](https://82.157.81.79/tasks/63) | 真实采集/分析、RAW大小及SHA通过 |
| PERF_CPU  | [64](https://82.157.81.79/tasks/64) | 真实采集/分析、RAW大小及SHA通过 |
| PYTHON_CPU native=False | [65](https://82.157.81.79/tasks/65) | 真实采集/分析、RAW大小及SHA通过 |
| MEMORY_TRACE category=pagefault, max_events=200, stack_depth=8 | [66](https://82.157.81.79/tasks/66) | 真实采集/分析、RAW大小及SHA通过 |
| MEMORY_TRACE category=mmap, max_events=200, stack_depth=8 | [67](https://82.157.81.79/tasks/67) | 真实采集/分析、RAW大小及SHA通过 |
| IO_TRACE category=syscalls, max_events=200, stack_depth=8 | [68](https://82.157.81.79/tasks/68) | 真实采集/分析、RAW大小及SHA通过 |
| PROC_CPU  | [69](https://82.157.81.79/tasks/69) | 真实采集/分析、RAW大小及SHA通过 |
| GO_CPU  | [70](https://82.157.81.79/tasks/70) | 真实采集/分析、RAW大小及SHA通过 |
| GO_HEAP sample_type=inuse_space | [71](https://82.157.81.79/tasks/71) | 真实采集/分析、RAW大小及SHA通过 |
| JAVA_CPU event=cpu | [72](https://82.157.81.79/tasks/72) | 真实采集/分析、RAW大小及SHA通过 |
| JAVA_CPU event=alloc | [73](https://82.157.81.79/tasks/73) | 真实采集/分析、RAW大小及SHA通过 |
| JAVA_CPU event=lock | [74](https://82.157.81.79/tasks/74) | 真实采集/分析、RAW大小及SHA通过 |
| JAVA_HEAP live=True | [75](https://82.157.81.79/tasks/75) | 真实采集/分析、RAW大小及SHA通过 |
| GPERF_CPU  | [76](https://82.157.81.79/tasks/76) | 真实采集/分析、RAW大小及SHA通过 |
| GPERF_HEAP  | [77](https://82.157.81.79/tasks/77) | 真实采集/分析、RAW大小及SHA通过 |
| JEPROF_HEAP  | [78](https://82.157.81.79/tasks/78) | 真实采集/分析、RAW大小及SHA通过 |
| RESOURCE_SNAPSHOT  | [79](https://82.157.81.79/tasks/79) | 真实采集/分析、RAW大小及SHA通过 |
| SCRIPT template_id=process_status | [80](https://82.157.81.79/tasks/80) | 真实采集/分析、RAW大小及SHA通过 |
| BOLT_PROFILE  | [81](https://82.157.81.79/tasks/81) | 真实采集/分析、RAW大小及SHA通过 |

[前8项及目标恢复](evidence/malloc-20261020/production-final-regression.json) · [后12项及恢复](evidence/demo-completion-20261020/final-runners.json) · [权重单位补证](evidence/demo-completion-20261020/final-runners-result-units.json)。缺页66为200次fault、bytes权重0合法；其验收器初判保留后按事件补验。HPROF按对象、资源按时序、脚本按exit/output验收，不强行要求通用栈权重。

malloc63真100alloc+100free/200非空栈。BOLT81真161samples、9制品与6组输出等价，CPU比0.9942无提升；无LBR的分支数据Web10明确未测量。两者桌面/375手机结果及制品已实际核对。最终Saber调查9/修复7见[现场指南](INTERVIEW_DEMO.md)。

计划/复合/取消和幂等重试也新增真实证据：[platform](evidence/completion-20261020/platform.json)；最终备份与隔离恢复见[收据](evidence/completion-20261020/final-backup.json)。三profile显式peer权限、单文件tracefs写、SYS_ADMIN缺失及正负控见[malloc实现](MALLOC_TRACE_COMPAT.md)。本轮没有重启主机或宣称长期/HA/ARM环境通过。

## 早期Core1的20场景与AI记录（历史）

下列任务17–48及Java/Go模型闭环在较早API9/Web7/Core1阶段完成，原始数据和失败保留。它们与上面的新任务分开，不能冒作同一版本的同时验收。原VFS/block场景没有在最终20个新任务中重复采集。

## 可展示的任务

| 场景 | 已成功任务 | 实际结果/口径 |
|---|---|---|
| C++线程CPU / perf调用栈 | [17](https://82.157.81.79/tasks/17)、[18](https://82.157.81.79/tasks/18)，页面新建[45](https://82.157.81.79/tasks/45) | procfs为线程CPU ticks；perf为209个采样，28节点；线程统计没有函数栈 |
| Go CPU / pprof堆 | [23](https://82.157.81.79/tasks/23)、[24](https://82.157.81.79/tasks/24) | 标准库真实pprof端点；CPU权重270000000ns；inuse_space采样权重4802021bytes，不是精确堆对象引用图 |
| Java CPU / 分配 / 锁竞争 | [25](https://82.157.81.79/tasks/25)、[26](https://82.157.81.79/tasks/26)、[27](https://82.157.81.79/tasks/27) | async-profiler实际采集，分别169/448/238权重；需按各事件口径解释，不能当请求耗时 |
| Java HPROF堆转储 | [28](https://82.157.81.79/tasks/28) | 真实6505884字节HPROF，经Analyzer解析；与Go分配采样是两种数据 |
| 独立Python CPU / 分配内存 | [29](https://82.157.81.79/tasks/29)、[30](https://82.157.81.79/tasks/30) | 非CRM合成负载；真实py-spy / memray attach，CPU484采样、内存2116096bytes权重 |
| C++文件I/O：syscalls / VFS / block | [31](https://82.157.81.79/tasks/31)、[33](https://82.157.81.79/tasks/33)、[48](https://82.157.81.79/tasks/48) | 真实内核追踪；block使用有界磁盘文件，2752512bytes权重；tmpfs原任务34无块事件的失败保留 |
| 缺页 / mmap分配与释放 | [35](https://82.157.81.79/tasks/35)、[37](https://82.157.81.79/tasks/37) | 3952个未分类缺页（未区分major/minor）；mmap246次分配及246次释放。缺页字节权重0正确，按事件数验收 |
| gperftools CPU / heap / jemalloc heap | [39](https://82.157.81.79/tasks/39)、[40](https://82.157.81.79/tasks/40)、[42](https://82.157.81.79/tasks/42) | 显式接入native profiler控制协议的真实受控程序；不能对任意未接入进程直接套用 |
| 资源快照 / 进程状态脚本 | [19](https://82.157.81.79/tasks/19)、[20](https://82.157.81.79/tasks/20) | 展示CPU/RSS/FD等时序及固定process_status诊断模板，不提供任意shell；无采样权重不等于无数据 |

这些新增多语言负载均是明确标记的合成程序，执行真实采样与解析，不是导入profile；不要作为真实业务事故或生产压测收益讲述。原Python CRM配色函数的完整隔离修复演示另见[现场指南](INTERVIEW_DEMO.md)。

## CRM之外的真实Agent闭环

| 场景 | 原任务→自动复测 | 诊断→再次诊断 / verification | 实际耗时 |
|---|---|---|---|
| Go CPU | 23→32 | 15→16 / 7 | 36.5秒 |
| Go heap | 24→36 | 17→18 / 8 | 24.4秒 |
| Java CPU | 25→41 | 19→20 / 9 | 42.5秒 |
| Java自然语言从零调查7 | 46→47 | 21→22 / 10 | 公网页面78.8秒 |

前三项调用真实DeepSeek和证据工具，由用户确认复测后平台自动采集、再次诊断与比较。第四项实际从公网页面选择java进程、输入问题，模型通过list_collectors/propose_collection选择JAVA_CPU/event=cpu/10秒99Hz，页面确认后自动完成后续闭环。四项均成功，但比较保持root_cause_status=not_tested；重复采样不能证明因果。当时源码行定位、自动补丁及隔离A/B仅支持CRM；现在另有Saber FTS5 preset，仍不能扩大为任意Go/Java项目自动修复。

现场操作：进入[智能性能调查](https://82.157.81.79/investigations)，选择在线节点tencent-local及当前java或drop_go_demo进程，描述CPU热点、时长及复测目标，勾选统计发送/采集授权。无需选择CRM按钮或勾选CRM源码修复。核对生成的采集器、目标身份、参数后确认。PID会随容器重建变化，以实时进程列表为准。

Java示例问题：

> 这个Java程序CPU占用偏高，请根据节点实际能力选择JVM的CPU函数调用栈采集器，采集10秒99Hz，检查质量、热点及调用者，自动复测并再次诊断核对。重复采样不能确认因果。

## 历史失败与验收口径

- BOLT43/54历史采集失败保留；现已定位并修复namespace内ELF build-id/MMAP身份聚合链路，57及最终81通过，见[BOLT](BOLT_DEMO.md)。
- malloc38历史附着失败保留；现已修复legacy单uprobe权限/事件名及双向AppArmor peer，最终63通过真malloc/free栈，不以其他内存采集替代。memray61策略退化也保留，最终62通过。
- 初次对CPU程序做I/O及缺页追踪（21/22）无对应事件，明确失败。block的tmpfs目标34无事件，改为有界磁盘负载后48通过。原记录保留。
- 原验收脚本误用“字节权重>0”判断缺页任务35，已按实际3952事件及RAW哈希补验。languages.json保留原断言失败，refinement.json给出正确口径，未改写任务历史。
- 此历史20项不含计划/DAG/取消重试/备份恢复；本次补证见页首，完整生产角色矩阵与长期稳定性仍不冒称通过。

## 早期部署与原始证据

7个受控演示容器已运行，24个项目容器均running且无OOM/unhealthy，7个监控target up，HTTPS readiness200。新Go目标go build、Java javac及C++ -Wall -Wextra -Werror编译实际通过；docker compose config --quiet通过。命名镜像及数据卷保留，没有down -v。固定命名镜像不覆盖，演示profile加入部署配置。

- [汇总](evidence/demo-scenarios-20261020/summary.json)
- [基础链路](evidence/demo-scenarios-20261020/basic.json)、[多语言及失败](evidence/demo-scenarios-20261020/languages.json)、[缺页与block补验](evidence/demo-scenarios-20261020/refinement.json)
- [三种真实AI闭环](evidence/demo-scenarios-20261020/ai.json)、[Java自然语言现场调查](evidence/demo-scenarios-20261020/java-natural.json)
- [19视图浏览器检查](evidence/demo-scenarios-20261020/browser.json)、[部署就绪](evidence/demo-scenarios-20261020/deployment.json)
