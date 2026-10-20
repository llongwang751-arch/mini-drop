# 实际大图性能基线与优化

2026-10-16，在 Windows x64 / i7-14650HX / 24逻辑CPU / 15.71GiB 内存、Chromium156 headless、1440×1000、无网络或CPU限速条件下访问阿里云公网部署。每种规模仅创建一个明确标记为合成导入的任务，优化后复用同一任务，不重新生成或模拟结果。

| 数据 | 10k节点，任务9 | 49k节点，任务10 |
|---|---:|---:|
| 实际folded输入 | 202,760 bytes | 991,160 bytes |
| 真实分析节点 / 最大深度 | 10,000 / 101 | 49,000 / 124 |
| 导入栈条数 / 采样权重 | 100 / 300 | 400 / 1,200 |
| Analyzer开始→成功事件间隔 | 130 ms | 602 ms |
| 优化前：Worker往返 | 72.2 ms | 286.9 ms |
| 优化后：Worker往返 | 91.0 ms | 304.3 ms |
| 优化前：导航→图可交互 | 1,063.7 ms | 2,655.3 ms |
| 优化后：导航→图可交互 | 860.3 ms | 1,468.8 ms |
| 优化前：SVG实际矩形数 | 10,000 | 49,000 |
| 优化后：初始可视SVG矩形数 | 2,102 | 8,402 |
| 优化前：输入搜索→绘制 | 190.1 ms | 769.0 ms |
| 优化后：输入搜索→绘制 | 58.8 ms | 145.4 ms |
| 优化后：矩形实际像素高 | 25 px | 25 px |
| 优化后：frame Tab入口数 | 1 | 1 |
| JS异常 / 失败HTTP请求 | 0 / 0 | 0 / 0 |

Worker仍布局完整10k/49k节点；DOM只呈现当前纵向可视层及3行overscan。初始滚动到底部显示root及主要层级；滚到顶部时root移出DOM、深层节点按需绘制。SVG固定像素高度，取消按深度扩展横向minWidth带来的纵向行高放大。搜索仍扫描完整布局，缓存规范化名称与颜色，使用memo节点和SVG搜索状态减少逐节点更新。

两种规模均实际验证：屏外函数显示1处匹配但当前DOM为0处；定位后真实聚焦该叶节点；重置恢复完整树；展开/折叠正常；方向键切换frame焦点；大树只有1个frame Tab入口。4项新单元回归与全部16项Web单元通过，TypeScript及Vite构建通过。

分析期间顺序执行，单任务输入均小于1MiB。独立SSH事后观察Analyzer为108.4MiB/384MiB、restart_count=0、oom_killed=false；这是事后快照，不是内存峰值测量。

测量定义：Worker往返含结构化克隆、调度与布局；可交互时间含网络、JSON解析、React提交及两次animation frame；搜索从真实input事件至匹配样式提交及两次animation frame。Analyzer耗时来自同一真实任务的历史开始/成功事件，优化后没有重跑后端。每组为单次测量，可能受缓存与实时负载影响，不构成SLA。

证据：

- `flame-performance-before.json` 与原始 `flame-performance.json`：原始完整DOM基线。
- `flame-performance-after.json`：优化后完整布局、可视DOM、搜索与交互验证。
- `flame-large-before.png` / `flame-large-after.png`：49k节点真实页面截图。
- `web/e2e/flame-performance.mjs`：独立执行脚本，默认复用本地checkpoint。可用 `DROP_FLAME_TASK_IDS=9,10` 指定既有任务、`DROP_FLAME_REPORT_LABEL=after` 单独保存结果，不需要重新登录。
