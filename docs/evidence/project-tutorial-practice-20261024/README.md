# 22 章教程：本地实作验证

`validation.json` 记录本轮内容规模、实际检查、修正经过、边界与文件 SHA。此前十章版本保留在相邻的 `project-tutorial-20261024`，未覆盖旧记录。

- `content-audit.json`：22 章围栏外汉字/章节/代码块、102 个源码入口存在性与 Python 围栏语法；语法检查不等于执行所有示例。
- `mechanism-labs.json`：十二个固定合成机制实验、88 个实际断言。临时 SQLite 仅验证本模型事务，不证明 PG 或真实网络；checkpoint 模型不是磁盘持久化。
- `mini-smoke.json`：真实受控自身 CPU 采集、Go/Python 文件交接、SHA/单位/树值、HTTP 拒绝/重放、重启和第二轮，20 检查通过。计账树没有函数栈。
- `attempt-mutation.json`：独立临时教学源码副本删掉 finish 的 attempt 条件，指定回归按预期失败。负控成功不表示错误版本可以交付，原始源码未改坏。
- `web-unit.json`、`browser-tests.json`：162 单测及 8 浏览器测试的原始报告。工作台响应丢失测试使用明确合成传输夹具，不作为真实采集记录。
- `browser-real-chain.json`：独立本地浏览器实跑，绑定本次创建任务，核对真实 RAW 摘要、来源与第二轮；9 检查通过。工作台和阅读页截图另存 PNG，手机证据截图使用新建的 375px 页面。

没有运行生产服务、付费模型、完整 Docker/PG 栈或外部 PID 采集。真实数据只来自这份受控教学程序。初始编译参数、验证脚本与格式告警的修正经过在 validation 中保留，不把后续通过表述为首次全部成功。
