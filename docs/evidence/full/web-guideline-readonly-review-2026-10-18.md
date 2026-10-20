# Web 指南映射复核（只读，2026-10-18）

复核当前冻结的92项Web输入与指南§8，没有访问服务器、运行浏览器或改动业务JS/dist。本机96项unit、21项导入fixture浏览器和7项安全IO辅助仍属于原报告，本次不冒报新UI验收。Awwwards/Webby/FWA是用户的质量目标；截图目检和axe通过不等于获奖认证。

| 要求 | 代码实际行为 | 真实限制 |
|---|---|---|
| §8.3、8.5 路由/表单 | 实际API查询驱动Agent/任务/组/规则/计划/DAG；NewTask按TaskKind、Agent能力和schema生成；导入明确标记来源，共享操作读取API权限字段。 | 静态检查和导入fixture不能证明最终helper支持全部现场采样；真实全站尚未执行。 |
| §8.6 阶段/失败 | TaskDetail上方为4阶段，独立timeline列实际事件和attempt；失败展示error code/message，任务ID在标题。 | 未单列结构化failed_stage/retryable/recommended_action。result query只在analysis SUCCESS时开启；RAW/log制品可访问，但不等于FAILED时已渲染partial分析视图。 |
| §8.6 最后事件图标 | timeline按含FAIL的事件加failed类，但最后一项不区分事件类型均画Check。 | 最后若为TASK_FAILED/ANALYSIS_FAILED会出现红色check，可能误导完成语义；旧96/21不能冒称已覆盖此状态。 |
| §8.8 gzip/Worker | profile Worker处理授权、精确过期刷新、有界下载/解压/JSON；d3-hierarchy布局Worker、SVG裁剪，全部100k保留。浅图高度和细帧说明已有实际回归。 | rc4仅改Nginx层；实际包版本不证明gzip真实传输、页面Worker或完整Grype门禁。需CE seed+browser两步及全站验收。 |
| §8.9 Markdown | react-markdown的skipHtml、元素/URL白名单、外链noopener/noreferrer/no-referrer和字符上限；已有HTML/危险链接安全回归。 | 没有使用指南指名的DOMPurify库，是同一安全目标的不同实现，不能写成依赖名称逐字一致。企业模型未配置时按钮明确禁用，不能声称真实模型已接通。 |
| §8.10 公开配置 | 浏览器同源/api/v1；Guide提供公开接入说明；模型公开开关来自auth/providers。未发现长期对象Secret、数据库口令或TLS私钥注入bundle。 | 没有独立config.js热更新环境/帮助开关；指南本段限定公开信息范围，不能单凭没有config.js判定必需能力缺失。 |
| §8.11 验收 | 本机fixture已有权限、表单、幂等、轮询/SSE恢复、过期链接、100k及安全Markdown证据。 | 不把fixture、纯回归、准备脚本或构建镜像称为真实全量stage通过；Cron编辑器验收也不等于实际DST到点触发。 |

真实stage联合入口见`stage-browser-execution-plan-2026-10-18.md`。这些限制不表示用户接受删减范围；本次按只读授权保留冻结产物，由主任务安排修正和最终验收。

## 后续 rc5 修正与实际本机验证

以上表格保留当时只读快照。经授权，§8.6已实现七阶段、准确失败/取消图标、阶段/重试事实/建议/追踪ID，以及严格当前采集尝试的RAW/LOG/BINARY已保存文件面板；不把旧尝试或分析重试前的RESULT/MANIFEST补作当前失败结果。API的成功分析结果接口保持原授权与attempt约束，FAILED没有完整partial分析JSON时明确说明。§8.9已接入DOMPurify 3.4.17，与原remark解析链、URL白名单及外链属性协同净化，安全降级为转义文本。

rc5实际完成110项单测、23项本机导入fixture Chromium回归（39.187秒，零skip/flaky）、7项安全下载辅助回归、TS/spec TS/Prettier/生产构建及官方npm生产依赖审计（100 prod、0已知漏洞、exit 0）。最终375/1440失败卡内边距和紧凑结果说明已重截、实际目检并通过axe/键盘/无全页横溢检查。详见`web-rc5-source-freeze.json`和`../../UI_REVIEW.md`。

rc5源码、bundle与上下文已独立冻结，旧rc4产物保留。上述结果仍是本机界面验证；真实stage全站、真实MinIO Content-Encoding:gzip经浏览器identity传输与Worker联调、最终镜像安全门禁和公网发布尚未由本报告证明。
