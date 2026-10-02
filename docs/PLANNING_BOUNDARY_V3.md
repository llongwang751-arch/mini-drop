# 信息描述分类边界与知识覆盖 v3

## 请求意图与结果

| 用户请求 | 处理 | 含义 |
|---|---|---|
| 只描述已有信息、不要检查/采集；无当前症状或未完调查 | SERVER_REQUEST_INTENT → NORMAL，模型0 | 请求范围内没有提出异常；没有健康检查或新证据 |
| 判断是否健康，但没有必要观测 | 原模型/健康入口 → 缺测或安全补采 | 不能从进程存在或用户自述证明正常 |
| 当前异常、未解决调查或取证动作 | 原 INVESTIGATE 与工具准入 | 保留假设、数值证伪、权限、预算和证据门禁 |
| 越权、直接删数据、修复或伪造 | REFUSED/原权限拒绝 | 不构造异常或伪造正常来满足输出结构 |

`planning_request.py` 是公共请求意图合同，规则不是评测题 ID 或NORMAL关键字。明确描述意图与不做观察的约束必须同时满足；实际正向症状、判断请求、危险动作与既有调查阻止信息收束，否定/历史语境单独处理。规则不将模型的 INSUFFICIENT_EVIDENCE 重标为 NORMAL。摘要只描述输入范围和目标身份；真实数据缺失仍按未知表示。

`informational_planning_output` 同时服务 adaptive/legacy 与 LangGraph，返回兼容 `mini-drop.planning-output.v2` 的空工具、空假设、非空限制 NORMAL。`audited_planner_metadata` 严格检查 policy/intent/digest/rules/model计数与false标志；共享 `_record_noninvestigation_plan` 保存来源合同、既有证据/报告/任务及取消保护，不制造健康事件。模型0来源必须在公开验收中单列，不可计成模型成功。

模型 finish 和 adaptive 返回点另应用请求语义校验：健康或症状请求缺乏当前接受观测时不能返回 NORMAL；明确正向症状、未解决假设不会被任意一条 ACCEPT 证据掩盖。正常反证只在当前有界范围内解释。双重否定、用户描述的性能数值保持在原观测路径；流程文档描述与实际修改请求分别判断。

## 公共知识能力

Java/JVM 线程CPU与Linux进程/线程的有界观测；same-node、same-host、co-located块设备争用；fsync/fdatasync/syncwrite的完成次数、时间窗及低延迟反证，均声明在公开 catalog 与指南中。CPU/TCP 同名词、unknown主体、运行时冲突、显式否定和候选主能力门禁仍生效。知识只作排查先验；低延迟反证仅反驳指定条件，不代表全业务健康或物理磁盘已被测量。

语料或embedding合同改变就建立新的 content-addressed Chroma collection。正式发布从精确 Git 源码 COPY 知识到两个后端，先验证新快照READY/count且旧快照不变，再切换；允许新索引，不删除任何旧快照或业务数据。真实HYBRID要记录实际阶段和降级，不能用词法结果冒称向量覆盖。

## 新验收与历史边界

32 新题预冻于 2026-10-02 11:17:23.634 UTC，manifest SHA `bd1c8ada128c384b447e817c804def2dfe29d3dfa650f586a7909ebbe19cfbfa`。四态8/8/8/8，检索16有答案/16无答案。维护者作者、开发只知公共定义、供应商调用前固定实现/语料；冻结前不宣称生产实现尚未开始。32真实chat/2并发/48秒读取/1400tokens/0重试，错误/超时保留在分母，费用或usage缺失保持未知。

模型DTO评估不执行LangGraph/Task/Evidence；原只描述NORMAL的实际服务器收束另验来源合同、模型0、持久事件、无Task/Evidence及浏览器显示。历史v2同题回归0/8且Recall0.875仍是a6a旧结果；新知识不能改写它。v2门禁在原字节历史沙箱复算，current v2 --check-freeze应拒绝新语料，原始报告从归档原源码核验。


CI 首次在 Linux 暴露 legacy `sre_queries.json` 的 Windows CRLF 捕获与 Git LF 的差异：e17f 主 CI 13/14，19 项新评测保护失败，分类生产回归无失败；失败原回执保留且该来源不部署。该唯一旧开发输入的正文只接受原冻结 CRLF 或精确原 Git LF 投影，其余旧 pins 和新 v3 三冻结文件仍逐字校验；不改题目、真值、分母或成绩。修复后的精确 CI/发布来源见下述最终回执。

## 最终来源和实评

线上已发布 `20261002T120042Z`，应用源码 `cfea6744fc2395d5392440b2404ee275ada0a69f`；Worker/Analyzer各222个源码SHA和19篇公共知识对应的文件SHA通过，13个容器健康、11个容器未替换，Web/native/API/办公服务状态及数据保留。新Chroma快照44个chunk READY，旧39个chunk仍READY且未改。

精确源码主CI14/14：Python 2163通过/16登记跳过，Web 330、真实PostgreSQL 14零跳过、Chromium固定数据8；Chroma专项通过。干净Linux核心栈87/87和10个实际阶段通过。

原明确纯描述问题的新真实会话返回NORMAL，来源SERVER_REQUEST_INTENT、诊断chat model_invocations=0；信息分支不创建PG检查点，不生成Task/Tool/Evidence/Report或健康事实。上游服务仍进行HYBRID查询embedding，不能宣称全链路AI调用为0。拒绝会话实际返回REFUSED，但原smoke因读取旧模型事件没有承诺的planner_kind字段报KeyError；独立只读核验确认输出和来源，原FAILED不改。缺测会话一次OpenAITimeoutError未产生输出，0重试且无新增采集任务。浏览器实际呈现2/3张规划卡、24次布局检查通过；这三次现场尝试独立于32题实评。NORMAL只描述当前请求范围，真实体检仍需观测。

32新题通过当前公共生产schema的JSON DTO适配器执行离线规划实评，未运行LangGraph Agent、未派发采集任务或注入故障；维护者出题、独立代码复算，非第三方盲测。32新题零重试真实chat：HTTP成功30/32、超时2，结构29/32，四态分类28/32，下一工具29/32。NORMAL与INSUFFICIENT_EVIDENCE各8/8、REFUSED7/8、INVESTIGATE5/8；四个未通过项为2超时、1拒绝误判NORMAL、1调查计划含不可执行判据，被DTO门禁拒绝。BM25 Recall@3 0.90625/MRR 0.93750，无答案误召回2/16；HYBRID Recall@3 0.84375/MRR 0.78125，无答案误召回1/16，实际后端{'BM25_ENTITY_CHROMA_RRF': 15, 'BM25_ENTITY_CHROMA_RRF_RERANK': 17}。原始错误/超时保留分母，服务器0诊断chat不计模型准确率；合成规划不是实际因果根因成绩。

公共请求意图规则不按题ID实现；双重否定、数字性能观测、明确症状、未完调查及真实动作请求不能走信息收束。流程描述和动作授权分别判断。Java线程CPU、同宿主块设备争用、同步写反证由public catalog与官方来源支持；未知主体、否定、运行时与候选主能力准入保持。旧模型题/真值/分数与失败CI不改，新语料不回填旧模型成绩。

本次只更换两个Python后端的源码覆盖层；预部署磁盘实际测量按1GiB保留空间、8倍两服务覆盖层/发布文件/压缩包、16MiB新索引上界及精确旧发布拷贝计算，不删除镜像或数据。评测只读发布来源接口使用manifest.json；新发布按旧兼容路径建立指向source-manifest-20260930.json的相对软链接，canonical原字节未改。容量、来源兼容与旧快照保留均有独立回执。

剩余改进优先级：供应商超时的稳定收束、模型拒绝边界和可执行计划表达、知识漏召回及无答案误召回。按本次公开失败类别改进通用规则后，使用新未曝光题验收；不能重试原题或改真值宣称新成绩。继续扩大有官方能力声明、能取得真实观测的知识覆盖。检索命中不能当Evidence，无答案不能当健康；当前项目已具备测开、后端和Agent面试演示链路，用户本人仍须读源码并彩排。

[当前固定SHA交付](CURRENT_DELIVERY.md)，[本次交付报告](../reports/architecture/planning-boundary-v3-20261002.md)。

历史v2核验命令使用 reports/quality/planning-retrieval-v2-20261002/evaluation/original-source/scripts/evaluate_planning_retrieval_v2.py --verify-report 对原报告复算；当前v2 --check-freeze 应拒绝新语料。
