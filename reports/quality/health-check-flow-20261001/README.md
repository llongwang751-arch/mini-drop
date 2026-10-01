# 当前状态检查交付证据

发布 20261001T140111Z；后端 Git 源码 0f61e17129d3cc9bf92dfd19fd6e6e867787ebfa。Web 采用此前冻结的新版界面加本轮变更，完整来源见 web-source-manifest.json；不能将整个 Web 包称为该 Git 提交的精确 CI 构建。

三次真实 Office 检查均 NORMAL_OBSERVED / COMPLETED，根因报告0份，6份真实原始产物下载 SHA 一致。两次后续检查创建独立会话；第三次由真实浏览器“再次检查”按钮触发。进程未重启，后续绑定采用更新的进程快照。异常/观测不足分支由回归测试验证，本轮未进行真实异常注入，不能计为新的21类或因果验收。

CI 36873505926 成功13/13；Python 1418通过/10登记跳过，PostgreSQL8通过/零跳过，Git Web307通过。实际冻结Web308通过、生产构建与体积门禁通过；修正测试选择器后专项12通过。首次CI的测试布局假设失败、后端/前端开发期失败与真机校验脚本字段错误均保留。

Worker/Analyzer各204份文件SHA与58份容器/公网Web文件SHA一致。13容器健康，其他10容器ID及Office进程/发布保持；21故障inactive，旧因果0/21及此前工程诊断3/3成绩保持。浏览器四种宽度、历史路径/I/O反证、前进后退、9份原有工程证据下载SHA验证通过，无JS/HTTP错误。

manifest.json覆盖本目录其余文件的实际字节。Git代码、实际Web变更、原始API记录、失败和复验分开保存；不修改历史证据。大体积CI ZIP仍保留于 output/acceptance/health-check-flow-20261001/ci-artifacts/，下载元数据和主报告已归档。
