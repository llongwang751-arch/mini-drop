# 分类边界与知识覆盖交付

线上已发布 `20261002T120042Z`，应用源码 `cfea6744fc2395d5392440b2404ee275ada0a69f`；Worker/Analyzer各222个源码SHA和19篇公共知识对应的文件SHA通过，13个容器健康、11个容器未替换，Web/native/API/办公服务状态及数据保留。新Chroma快照44个chunk READY，旧39个chunk仍READY且未改。

精确源码主CI14/14：Python 2163通过/16登记跳过，Web 330、真实PostgreSQL 14零跳过、Chromium固定数据8；Chroma专项通过。干净Linux核心栈87/87和10个实际阶段通过。

原明确纯描述问题的新真实会话返回NORMAL，来源SERVER_REQUEST_INTENT、诊断chat model_invocations=0；信息分支不创建PG检查点，不生成Task/Tool/Evidence/Report或健康事实。上游服务仍进行HYBRID查询embedding，不能宣称全链路AI调用为0。拒绝会话实际返回REFUSED，但原smoke因读取旧模型事件没有承诺的planner_kind字段报KeyError；独立只读核验确认输出和来源，原FAILED不改。缺测会话一次OpenAITimeoutError未产生输出，0重试且无新增采集任务。浏览器实际呈现2/3张规划卡、24次布局检查通过；这三次现场尝试独立于32题实评。NORMAL只描述当前请求范围，真实体检仍需观测。

32新题通过当前公共生产schema的JSON DTO适配器执行离线规划实评，未运行LangGraph Agent、未派发采集任务或注入故障；维护者出题、独立代码复算，非第三方盲测。32新题零重试真实chat：HTTP成功30/32、超时2，结构29/32，四态分类28/32，下一工具29/32。NORMAL与INSUFFICIENT_EVIDENCE各8/8、REFUSED7/8、INVESTIGATE5/8；四个未通过项为2超时、1拒绝误判NORMAL、1调查计划含不可执行判据，被DTO门禁拒绝。BM25 Recall@3 0.90625/MRR 0.93750，无答案误召回2/16；HYBRID Recall@3 0.84375/MRR 0.78125，无答案误召回1/16，实际后端{'BM25_ENTITY_CHROMA_RRF': 15, 'BM25_ENTITY_CHROMA_RRF_RERANK': 17}。原始错误/超时保留分母，服务器0诊断chat不计模型准确率；合成规划不是实际因果根因成绩。

公共请求意图规则不按题ID实现；双重否定、数字性能观测、明确症状、未完调查及真实动作请求不能走信息收束。流程描述和动作授权分别判断。Java线程CPU、同宿主块设备争用、同步写反证由public catalog与官方来源支持；未知主体、否定、运行时与候选主能力准入保持。旧模型题/真值/分数与失败CI不改，新语料不回填旧模型成绩。

本次只更换两个Python后端的源码覆盖层；预部署磁盘实际测量按1GiB保留空间、8倍两服务覆盖层/发布文件/压缩包、16MiB新索引上界及精确旧发布拷贝计算，不删除镜像或数据。评测只读发布来源接口使用manifest.json；新发布按旧兼容路径建立指向source-manifest-20260930.json的相对软链接，canonical原字节未改。容量、来源兼容与旧快照保留均有独立回执。

剩余改进优先级：供应商超时的稳定收束、模型拒绝边界和可执行计划表达、知识漏召回及无答案误召回。按本次公开失败类别改进通用规则后，使用新未曝光题验收；不能重试原题或改真值宣称新成绩。继续扩大有官方能力声明、能取得真实观测的知识覆盖。检索命中不能当Evidence，无答案不能当健康；当前项目已具备测开、后端和Agent面试演示链路，用户本人仍须读源码并彩排。

[当前固定SHA交付](../../docs/CURRENT_DELIVERY.md)。

证据包含首轮Linux换行保护失败和修复、原始模型/检索记录、实际服务器来源和PG检查点、CI及部署SHA；完整清单见[SHA清单](../quality/planning-boundary-v3-20261002/manifest.json)。
