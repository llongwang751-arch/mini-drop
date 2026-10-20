# Drop 前端改版与自检

日期：2026-10-16。用户选择“浅色精密仪表台”；最终前端版本20261016.3，后端仍为20261016.1。

以视觉、使用体验、交互和内容真实性作为审查重点；Webby的[官方评审标准](https://www.webbyawards.com/judging-criteria/)同样将视觉、结构导航、交互、功能和整体体验纳入评估。这里记录具体改动与可复查证据，不给自己生成奖项评分。

| 维度 | 本轮实际调整 | 自检方式与结果 |
|---|---|---|
| 排版 | 更大中文标题、14px任务名、12px辅助信息；删除6–9px碎字和垂直英文品牌字 | 桌面/手机逐页截图；200%纯文字检查后修复类型与状态重叠 |
| 留白 | 统计横带替代四张等权卡，终态流程压为细条，空洞察按内容高度排版 | 首屏可直接看到真实最近诊断；手机刷新与图表工具不再额外占整行 |
| 视觉层级 | 最近诊断为主栏，节点/队列为侧栏；详情以火焰图为主，边界说明可展开 | 保留采集/分析双状态；热点零值过滤及数量提示，不删掉真实限制信息 |
| 色彩 | 浅色画布、白面板、墨色导航、蓝操作色；暖色保留给火焰图 | 首版辅助灰对比不足后修复；最终13组公网页面/宽度axe全部violations为空 |
| 动效 | 180–240ms操作/菜单过渡，取消数据区反复入场，减弱动效禁用过渡 | 公网E2E覆盖reduced-motion；文本保持不透明，无持续装饰动画 |
| 微交互 | 柱图真实小时读数、左右/Home/End键、单一Tab入口，表单选中/菜单展开ARIA，抽屉焦点返回 | 键盘与抽屉inert/Escape通过；大图展开/收起在底部保持底部，中间位置保持原位置 |
| 响应式 | 手机任务改记录布局，短屏侧栏内部滚动，火焰图保留1000px画布避免横向压字 | 375/768/1024/1200/1440及812×375横屏；修复后无全页横滚，200%文字无名称/类型/状态重叠 |
| 原创性 | 原创SVG层叠切面Drop标志，概览围绕实际诊断记录编排，删除通用营销卡 | 图形为品牌表达，不伪装采样；趋势、节点、状态、火焰树均来自实际后端数据 |

最终验证：16项Web单测、TS/Vite生产构建、Prettier通过；公网6项E2E通过（26.8秒）；公网13组页面/宽度扫描零axe告警、零JS/HTTP异常、无页面溢出。独立响应式报告保留原失败坐标与限定复测结果。

10k/49k既有任务的大图原脚本通过，未增加滚动操作规避问题。完整树仍为10,000/49,000节点，首屏SVG矩形2,102/8,402；49k本次交互1,703ms、搜索89.1ms。单次客户端测量受缓存/负载影响，不作为SLA。

证据入口：

- [新版首屏](evidence/redesign/overview-first-screen.png)、[手机总览](evidence/redesign/overview-mobile.png)、[新版登录](evidence/redesign/login.png)、[新版详情](evidence/redesign/result.png)。
- [旧版总览](evidence/redesign/before-overview.png)、[旧版登录](evidence/redesign/before-login.png)。
- [公网逐页扫描](evidence/redesign/preview-report.json)、[独立响应式与放大复核](evidence/redesign/responsive-final.json)、[发布快照](evidence/redesign/ui-release-20261016.3.json)。
- [大图回归](evidence/flame-performance-redesign.json)、[展开锚点](evidence/flame-expand-anchor-redesign.json)。

前端改版没有补齐全部后台能力；具体范围见[能力差距](CAPABILITY_GAPS.md)。

## 全量版本开发自检（2026-10-17，尚未发布）

延续浅色仪表台，补齐批量操作、完整任务对比、18种契约驱动表单和类型结果页。详情先显示主图与规则，再显示尚未配置模型的辅助区域；原生导入一律写“数据导入”，未知时长/频率/丢样保持未知。

高级火焰图折叠收纳筛选、线程证据及函数调用关系，保持主诊断层级；统一调用关系按钮样式，窄屏表格只在自身容器横向滚动并可键盘聚焦。375/1440显式导入fixture浏览器检查axe零违规、无横溢和JS异常。配色/排序丢失已聚焦子树的问题已实际复现并修复，同root重新布局保留中段滚动和底部锚点。

49项完整单测、生产构建及高级交互/10k/50k/100k共4项浏览器测试通过（17.7秒）。全部100k Worker节点保留，初始SVG23帧，离屏搜索、聚焦/重置和键盘实际通过；当前报告附源文件与bundle哈希。它们只证明本机界面行为，不代替新版真实后端联调、Analyzer吞吐或生产发布。

- [高级交互与可访问性](evidence/full/flame-explorer-fixtures/report.json)
- [三档规模与环境/资源边界](evidence/full/flame-scale-browser/report.json)

同轮请求生命周期增补后，最终Web为68单测、生产构建与10项本机浏览器回归全绿（27.7秒），入口index-DoEWqUpR。包含连续失败20/40秒退避及成功恢复、隐藏暂停、离页Abort、SSE精确Last-Event-ID/重连/403停止。三档规模和高级交互/axe报告已对这一实际bundle重跑；真实全栈批量/对比、Cron/DAG和公网发布仍待执行。

## rc5 失败诊断与 Markdown 自检（2026-10-18，尚未发布）

指南§8.6现补齐七阶段主流程、失败阶段、重试事实、操作建议及任务/执行尝试/请求追踪ID。失败与取消事件使用明确图标，成功勾选仅用于实际成功事件。当前尝试没有安全可读制品时直说尚未生成；有文件时仅列当前采集尝试仍可授权下载的RAW、LOG、BINARY。旧尝试文件不会补位，也不将这些文件称为完整分析结果。FAILED不请求仅支持成功分析的`/result`接口。

失败面板桌面左右24px、手机16px，标题与字段保持20/24px节奏；制品行沿用同一内边界，下载与操作触控区域至少44px。重复的无结果区域压缩为简短说明。375/1440最终截图已实际目检，浏览器检查包含面板内边距、按钮尺寸、无整页横溢、axe、Enter切换/重新分析与Space选择操作。

指南§8.9改为同一remark解析链生成惰性HTML，再由DOMPurify 3.4.17执行HTML profile及显式标签/属性白名单净化后渲染。原始HTML、危险协议、样式与iframe/svg/math禁用；外链关系在净化前设置，净化后不再改写HTML。代码内容保持文本语义，表格/代码仅在自身容器滚动；不支持安全净化时回退为React转义文本。浏览器实际覆盖攻击载荷、链接、表格、代码及净化器自身断言。

最终验证为**110项单测、23项本机Chromium浏览器回归（39.187秒，零失败/跳过/重试）、7项下载安全辅助测试**，TS、spec TS、Prettier及生产构建实际通过。测试使用1 worker、关闭trace/HAR/video；浏览器API/S3/模型响应均为明确标记的导入fixture，未连接新版真实stage。100k规模仍保留全部Worker节点。官方npm registry生产依赖审计实际exit 0，100项prod计数、0已知漏洞；该结论不覆盖Nginx或镜像OS。

新版本以98个源码/配置输入和14个构建文件统一冻结，入口`index-CjCARton.js`；bundle清单SHA256为`f82e7712ee926e791cbe4150ced6dcba09c06012d6a83845e9ef944925edff52`。独立rc5上下文保留旧rc4归档，不将本机回归重标为线上发布或安全扫描通过。

- [失败详情桌面](evidence/full/failure-fixtures-rc5/failure-1440.png)、[失败详情手机](evidence/full/failure-fixtures-rc5/failure-375.png)、[任务列表桌面](evidence/full/failure-fixtures-rc5/tasks-1440.png)、[任务列表手机](evidence/full/failure-fixtures-rc5/tasks-375.png)。
- [Markdown桌面](evidence/full/markdown-fixtures-rc5/markdown-1440.png)、[Markdown手机](evidence/full/markdown-fixtures-rc5/markdown-375.png)、[gzip 100k桌面](evidence/full/profile-artifact-fixtures-rc5/gzip-100000-1440.png)、[gzip 100k手机](evidence/full/profile-artifact-fixtures-rc5/gzip-100000-375.png)。
- [统一浏览器结果](evidence/full/web-rc5-browser-results.json)、[单测结果](evidence/full/web-rc5-unit-results.json)、[源码/构建/截图冻结](evidence/full/web-rc5-source-freeze.json)、[生产JS依赖审计](evidence/full/web-runtime-dependency-audit-rc5-2026-10-18.json)。

新版镜像构建及实际安全门禁、真实全站stage、真实gzip传输/Worker联调与最终公网切换仍由后续验收确认。界面评审按用户提出的获奖网站品质目标持续检查，不宣称奖项认证或未测浏览器的一致性。

## rc6 当前失败执行的部分分析（2026-10-18，尚未发布）

指南§8.6的部分分析现在有独立契约和界面，不再以原始文件代替分析摘要。只在当前任务已采集、分析失败，且任务版本、采集尝试、分析job/run、输入RAW标识及摘要全部吻合时查询专用`/partial-result`。面板标为“未完成分析 · 已解析摘要”，显示已解析计数与最多20个热点函数；失败状态、失败阶段、追踪信息和安全原始文件仍保留。快照没有包含规则、结果制品与最终提交成果，不据此断言后续阶段从未执行。完整火焰图、AI和对比继续只接受成功结果。

部分结果数据严格限16KiB，网络读取另留1024字节标准envelope余量；名称最多160个UTF-8字节并拒绝控制字符，所有数值有界，函数名由React作为文本转义。字段、任务版本或执行身份错误时不展示；409无检查点、403/404无授权和传输错误分别处理，不自动重试成功接口。重试、重新分析、取消与切换任务会取消并清理当前部分结果缓存；此前成功结果也不能在新失败状态下回显。

新面板沿用浅色仪表台的24px/16px内边距，长符号自动换行，完整执行身份折叠收纳，44px触控区域与键盘操作均已实测。375/1440导入fixture的axe违规为0、无整页横向溢出；攻击载荷只显示为文本。保留键盘焦点截图作为交互证据，另在失焦、回到页首并等待两帧后拍摄默认视觉图。默认桌面实际viewport复核了侧栏顶部及内部滚动位置；fullPage截图不代替此检查。

最终rc6实际验证为**131项单测、28项本机Chromium统一回归（49.25秒，零失败/跳过/重试）、1项独立默认视觉补充检查（4.243秒）、7项下载安全辅助测试**。TS、spec TS、Prettier、生产构建与61条HTTP路由/75个schema的契约核对通过。官方npm registry生产依赖审计exit 0、100项prod、0已知漏洞；不覆盖镜像OS。105个源码/配置/契约输入和14个构建文件已统一冻结，入口`index-BuNtlK9R.js`，bundle清单SHA256为`d73b79da367c1a536204f46c3ab979fd0ee7b501ce7b0c8d8a6fa2ba9808224e`。rc5上下文及原报告保留原字节。

- [默认桌面详情](evidence/full/partial-analysis-fixtures-rc6/partial-default-r3-1440.png)、[默认手机详情](evidence/full/partial-analysis-fixtures-rc6/partial-default-r3-375.png)、[桌面面板实际viewport](evidence/full/partial-analysis-fixtures-rc6/partial-default-r3-panel-viewport-1440.png)、[手机面板实际viewport](evidence/full/partial-analysis-fixtures-rc6/partial-default-r3-panel-viewport-375.png)。
- [保留键盘焦点的桌面图](evidence/full/partial-analysis-fixtures-rc6/partial-1440.png)、[手机图](evidence/full/partial-analysis-fixtures-rc6/partial-375.png)、[默认视觉位置报告](evidence/full/partial-analysis-fixtures-rc6/default-visual-r3-report.json)。
- [统一浏览器结果](evidence/full/web-rc6-browser-results.json)、[单测结果](evidence/full/web-rc6-unit-results.json)、[默认视觉补充结果](evidence/full/web-rc6-default-visual-results.json)、[源码/构建/截图冻结](evidence/full/web-rc6-source-freeze.json)、[生产JS依赖审计](evidence/full/web-runtime-dependency-audit-rc6-2026-10-18.json)。

本节验证使用明确导入/合成数据与模拟API，证明本机UI与边界行为。真实Analyzer先保存当前run检查点、后续失败、API授权返回、浏览器展示的完整链路仍待隔离stage验收；新版镜像安全门禁与公网发布亦未因这些测试自动完成。

## rc7 公网实际只读复核（2026-10-18）

本次有限发布已在公网实际复核，前端业务代码与rc6的14个bundle文件保持相同字节。发布时发现私有构建进程的umask使assets目录成为0700；rc7只修镜像内公开静态目录0755、文件0644，两份公开Dockerfile同步在COPY后规范化权限。隔离镜像以UID101、只读根目录、无外网、cap0、32MiB/noSwap实际启动Nginx，全部14个文件的HTTP字节SHA均与冻结bundle一致；未将单独healthz成功当作静态资源可用。

实际浏览器重新登录公网，同一Chromium串行检查1440/375的总览、任务列表、接入节点、新建页及本轮合成导入任务27，共10页、51项明确列出的页面/结果断言、20张脱敏截图。没有提交新任务或任务动作。所选节点实际只发布PROC_CPU/PERF_CPU，新建页据此收敛到两种在线采集方式。新合成任务真实下载并由Worker解码gzip火焰图，显示100 samples、5 nodes和80/20热点。旧任务1只读验证真实结果接口200及完成分析组件，不截图、不下载RAW、不输出旧函数名。

10页均无整页横向溢出，pageerror、HTTP 5xx、外联、非登录写入及越界制品请求计数均为0。已目检实际生产的总览和详情两种宽度、节点桌面、新建手机与任务桌面图；字体层级、卡片内边距和流程布局可读，旧任务/节点/用户身份已遮盖，合成详情和图表保持可见。验收脚本自身曾将UUID用户ID误当数字，并漏掉页面标题的装饰句点；仅修脚本并保留失败记录，没有改业务包或将这两项记作产品故障。

- [实际生产浏览器报告与失败历史](evidence/releases/20261018.1-limited/production-browser-smoke.json)
- [真实公网桌面详情](evidence/full/production-ui-smoke-10c46515cc024817a05820b33827c18b-e04aebf1-1733-4e60-a4a3-3eeba82dd4cb/detail-1440-full.png)、[真实公网手机详情](evidence/full/production-ui-smoke-10c46515cc024817a05820b33827c18b-e04aebf1-1733-4e60-a4a3-3eeba82dd4cb/detail-375-full.png)
- [rc7真实HTTP静态字节验证](evidence/full/web-rc7-http-assets-2026-10-18.json)、[独立验证器失败与修正历史](evidence/full/web-rc7-http-validation-history-2026-10-18.json)

本轮生产检查范围有限，未执行生产axe/键盘套件，也未代替暂缓的隔离全站stage、部分分析真实故障链或全部采集器验收。此前导入fixture的可访问性和规模回归证据继续单独保留。
