# 性能路径定位：修复、真实验收与边界

本轮把数值判据是否已测量、异常条件是否成立、是否具备独立因果对照分开记录，修复真实规划、采样和源码映射中的断点。新增结果是性能路径定位与范围内反证，历史21类因果根因0/21保留。

## 为什么原先有数据却无法通过

| 断点 | 现场或回归证据 | 修复与验证范围 |
|---|---|---|
| 互斥数值条件与覆盖率混用 | 异常条件成立时正常反证必然不成立，原匹配率可能只有0.5 | 新 `checked_ratio` 记录测量完整性，`matches` 记录真值，旧独立对照和覆盖率保留；完整合同须来自同一不可变产物 |
| 新验证对象未正确持久化 | 首次持久化回归84通过、2失败 | 显式传递到报告顶层 verification，修复后105通过；旧失败JUnit保留 |
| 实际规划名称不一致 | 第一批网络案例使用NETWORK_DEGRADATION，注册表只识别NETWORK_LATENCY | 注册别名对应HTTP耗时观测，不能据此覆盖TCP丢包或重传 |
| 中文显示规范器丢掉机器判据 | 第一批I/O计划把合法数值表达式替换为泛化文字，形成UNSUPPORTED_PLAN | 说明性文字仍要求中文，已注册完整数值表达式保持可执行；冻结输入前后复现单独保存，不能算新现场验收 |
| CPU累计路径与源码映射不足 | 外层runCPUFault累计占比略高于内部goCPUHotFunction，后者切片返回类型未映射 | 保留最多3条达到原20%阈值且有源码位置的业务路径；累计占比重叠不可相加，不代表各函数自身CPU成本 |
| I/O在采样前提前停止 | 64MiB tmpfs中511次成功写后1次失败，后续首尾计数不变；同限制旧镜像隔离复现重复出现 | 单文件上限改8MiB轮转；相同64MiB tmpfs、192MiB内存、0.65CPU下CI完成至少600次真实同步写且后续计数继续增长 |
| 本地通过不等于可部署 | 旧Compose环境标签失效，Dockerfile Go1.26.5与go.mod1.26.8不符，前两次候选部署失败 | 从实际运行配置恢复部署、对齐工具链，CI加入真实镜像构建；失败发生在替换前，旧服务保持健康 |

I/O失败后的文件系统快照已经释放占用，不能宣称该快照直接显示磁盘已满；应用也没有保留具体errno，不能把ENOSPC写成已记录事实。容量判断依据配置冲突、提前停止模式及受控前后复现。

## 报告合同与实现

`server/app/drop_insight/observation_verifier.py`验证可信、身份合格的系统产物，区分VERIFIED、REFUTED、CONFLICTING_OBSERVATIONS、INSUFFICIENT_OBSERVABILITY和UNSUPPORTED_PLAN。完整期望成立且反证不成立才验证异常观测。条件不成立不是独立CONTROL，缺失也不等于0。

`service.py`保留原 `coverage_ratio`、`independent_control_verified` 与 `matched_verification_status`。数值观测新增 `verification_kind=PERFORMANCE_OBSERVATION`、`claim_scope=BOUNDED_OBSERVATION`、`causal_root_cause_verified=false`。CPU使用已注册Profile加独立OS CPU合同；两个采样窗口不宣称完全同步。

`bottleneck_localization.py`与独立评分器 `scripts/evaluate_performance_localization.py`只认可合格的具体性能路径：CPU热函数、HTTP/下游耗时路径、目标应用open/write/sync/close路径。因果根因和同负载修复标志均为false，旧严格因果评分器没有放宽。

Go应用累计成功同步操作耗时，与操作计数在同一锁中快照；Analyzer计算新增耗时/新增成功次数，不包含循环休眠和工作锁等待。I/O计划预先固定平均至少10ms、至少5次新操作；默认文件在64MiB tmpfs，测量不能宣传为块设备压测。

## 发布与验收

核心后端发布 `20261001T094553Z`，源码 `c59aac566cedef6a239eb1d640ee8fb796524ae3`；[精确CI36844752903](https://github.com/llongwang751-arch/mini-drop/actions/runs/36844752903)成功13/13，测试merge `4f7a7d55095fac33008ef54b4ed90e8938352dd9`。Python1358通过、10项登记跳过，Web274通过，真实PostgreSQL8通过零跳过，Chroma专项、Linux Go race、真实镜像构建及连续I/O隔离验证通过。覆盖率与原门槛未降低。

只更新Worker、Analyzer、Web和Go demo；Worker/Analyzer各193份文件、Web56份文件SHA一致，其余9容器保持。API二进制SHA仍为 `ffb7bc11e5c7dbaa7165c717af90ecd726cd4cad6ebf1794f75602630128d2b3`。Office保持 `observer-20260930T145902Z` / `f37f44e0f43ea58720da3638658a82c65430c595`，本轮部署前后PID1650962、NRestarts=0；此前PID484342属于历史检查窗口，不能用来描述当前运行进程。旧发布与私有回滚配置保留。

第一批三条真实链路记录原样保存，41份原始下载SHA一致、取证/撤销/收束3/3。CPU只定位外层路径，网络与I/O未覆盖完整测量计划，按新具体路径合同评分0/3；它们不能被修复后的离线回放追认为现场成功。

第二批独立现场批次已完成。37份原始下载SHA一致，取证链、真实进程身份、撤销恢复、清理与会话收束均3/3；同一Go实例的宿主PID1862439、namespace PID1、start_ticks214713532贯穿三段独立快照。控制快照没有注入Agent Evidence。

| 新案例 | 实际观测 | 新合同结果 | 因果根因 |
|---|---|---|---|
| Go CPU | 945有效Profile样本；goCPUHotFunction累计94.6%、runCPUFault94.9%，独立OS CPU64.611% | 定位到具体函数与源码路径，覆盖1且有独立CPU对照；累计占比不能相加 | 未验证 |
| Go网络 | 新增请求窗口均值240.713ms；异常与正常反证均已测量 | 数值观测VERIFIED，checked_ratio=1；HTTP耗时路径LOCALIZED；旧匹配率0.5、独立对照false保留 | 未验证 |
| Go同步I/O | 同一产物684次新操作，平均0.081ms；停止前成功10232次、失败0且仍活动 | 三判据均已检查，REFUTED；未发现预声明的10ms慢操作异常，因此不计瓶颈定位通过 | 未验证 |

路径定位为2/3，而不是3/3异常通过。I/O的测量完整、反证成立是有效诊断结果；它不应被改名为慢磁盘成功案例。原严格Campaign仍0/3：CPU/网络完成旧执行合同，I/O以INSUFFICIENT_EVIDENCE终态收束被旧合同拒绝；该状态说明没有证明假设，不表示其测量缺失。旧21类因果0/21与归档内部链18/21保持，不合并两个批次。

新会话：CPU `insight_b043658db8444bf3a34dfedc69a4fa9e`，网络 `insight_c0e59fdba9ce4147bf9729b0535a5c43`，I/O `insight_3093704de5e4496ebc10b58379014b18`。单独健康检查 `insight_d9219470b9394bc7b9eb4e3c8768eb1f`只执行1个系统采集、两份原始下载SHA一致；实际15样本约14秒、CPU0.286%、RSS211.434MiB且增量0；真实浏览器显示已检查范围正常，不外推全部业务。

## 最终前端发布与真实浏览器

最新Web单独发布`20261001T112302Z` / `ebb1a0e6a3a7396db789bb8a0a47c734d1219aff`；[精确CI36854620615](https://github.com/llongwang751-arch/mini-drop/actions/runs/36854620615)成功13/13，Web294通过，Python1358通过/10登记跳过，真实PG8通过零跳过。后端保持上述c59源码与进程。Web56文件、Worker/Analyzer各193文件SHA一致，另外12容器未替换，13容器及API3依赖健康，21注入inactive，旧0/21保持。

实浏览器在核心发布后还发现两类产品缺陷，保留首次失败和同输入负向回归：完整I/O反证被后续主机证据不足报告盖住，初筛遗漏ACCEPT_COUNTER且无效窗口覆盖有效测量（旧源码5失败）；案例链接在React重放函数状态更新时丢失，首次列表失败也提前清空请求参数（旧源码2失败）。分别修复报告选择、可信反证与窗口准入，以及纯函数状态更新/成功列表后处理链接。完整测量反证不会升级为因果根因，其他已发现资源异常仍优先。

默认浏览器连续导航还复现SSE连接滞留：前序事件流未出现CDP结束通知，新列表请求超时；缓存禁用对照与页面生命周期修复均恢复导航，支持连接生命周期导致排队的判断（CDP缺少结束通知不能作为实际连接数）；只关闭浏览器前进后退缓存的同输入对照通过（不能当作默认浏览器验收）。新增pagehide关闭连接与计时器、pageshow按原游标恢复，暂停时禁止凭据事件和旧连接错误重连；旧源码生命周期回归2失败、修复后13项通过。最终默认缓存浏览器重新完成全部检查。

修复前后JUnit、真实会话冻结输入、浏览器失败截图和最终成功截图均归档。前一轮完整Web默认并发在本机发生内存不足/导入错误，首次缺依赖运行也单独保留为环境失败；它们不能冒充业务负向复现。限制本机测试工作进程为2后完整通过，CI覆盖率与判据不改，最终294项通过。

真实Chrome最终检查CPU/HTTP定位与I/O反证（0.081ms、684次、3/3已检查）、三个案例链接、健康检查范围、默认21项、工程4项及9份原始下载SHA；后退/前进也恢复到正确案例，四种宽度无横向溢出，无JS/HTTP错误。补充连接数断言曾误把CDP未发结束通知当作实际存活连接，原失败日志和截图保留为INSTRUMENTATION_ASSERTION失败；最终验收使用真实列表请求、案例结果与导航断言，不把该计数当活跃连接数。浏览器只是读取上述不可变案例，没有重新注入或拼接成绩。

面试可依次打开[CPU热函数](https://120.24.187.205/ai-diagnosis?case=drop_insight_v2%3Ainsight_b043658db8444bf3a34dfedc69a4fa9e)、[HTTP耗时路径](https://120.24.187.205/ai-diagnosis?case=drop_insight_v2%3Ainsight_c0e59fdba9ce4147bf9729b0535a5c43)、[I/O假设被反驳](https://120.24.187.205/ai-diagnosis?case=drop_insight_v2%3Ainsight_3093704de5e4496ebc10b58379014b18)，需要现有访问凭据与可信TLS链。说明“有界观测→具体路径→原因干预”的差别，结合旧代码失败/修复回归、真实PG竞争与持续采样解释测试开发能力。

原始材料与逐文件SHA见[完整归档](../quality/performance-localization-20261001/manifest.json)，[最终浏览器](../quality/performance-localization-20261001/deployed/browser-r10/result.json)，[新批次评分](../quality/performance-localization-20261001/deployed/final-grade/localization-acceptance.json)，[最终运行复核](../quality/performance-localization-20261001/deployed/final-runtime-verification.json)。源码与证据已提交；工作区其他线程的中文源码注释和学习指南正文增补不纳入本轮提交。

## 仍未完成的范围

1. I/O真实磁盘工作目录、持久性结果与可归属等待证据；默认tmpfs上的快操作不能强行变成磁盘瓶颈。
2. HTTP延迟拆分为依赖处理、连接建立和TCP传输；丢包/重传需要对应真实证据。
3. 固定输入、固定请求负载下针对原因的干预，以及输出质量、全部请求与CPU/耗时的同负载比较。
4. 内存生命周期、GC、锁、噪声邻居和排队等其余域的专用对照；注册信号域并不等于全部21类因果验收通过。

用户已接受一小时1/120窗超限用于面试，本轮没有重复发压，原严格FAILED与200ms门槛仍保留。4个工程缺陷回归继续独立展示，不并入AI根因成绩。
