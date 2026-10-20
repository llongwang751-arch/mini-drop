# 隔离stage本地浏览器验收顺序（已准备，未执行）

本说明只准备开发机执行入口，不启动SSH、Docker、stage或隧道。远端重队列由发布负责人统一协调。最终Ops/Analyzer安全门禁尚未被发布负责人接受，启动报告或候选已冻结不能替代安全扫描；等待根代理明确交接stage-ready后再执行。

## 发布负责人先完成的门禁

1. 接受最终七镜像的实际安全评估，并以精确image ID启动独立`dropfullstage`，确认生产容器ID不变、服务健康、Agent在线和helper真实采集边界已验；API、Analyzer、Ops均须为`20261018.1-rc4`，Web为`20261018.1-rc6`，平台Nginx为rc2；Core/Agent由负责人当前实际候选证据绑定。
2. 保持stage公开Origin为`http://127.0.0.1:18080`，并准备已验证的本地转发。两个浏览器入口都固定此Origin，不能复用于公网或HTTPS切换验收。
3. 只将现有stage管理员用户名和密码安全放入开发机`.local/stage_admin_username`、`.local/stage_admin_password`；不在聊天、命令参数、日志或证据里打印内容。
4. 按`scripts/validation/README.md`在独立stage管理容器执行`stage_ce_gzip.py`。它只创建本轮唯一导入fixture，复验当前task/attempt/artifact，条件复制同一对象的Content-Encoding:gzip并保持原压缩bytes/SHA、用户和其他系统元数据。不能仅凭该脚本success宣布浏览器产品回归成功。
5. 安全取回两份**公共**JSON到固定本地路径：`docs/evidence/full/stage-candidate-start-2026-10-18.json`（主机candidate-start-proof.json的健康/镜像摘要）与`docs/evidence/full/stage-ce-gzip-seed-2026-10-18.json`（管理脚本唯一成功JSON）。不取回原HTTP响应、cookie、signed URL或环境配置。startup证据必须在一小时内、项目dropfullstage、精确七候选image ID、生产未变；Web rc6/平台须与已构建image ID吻合。当前Web rc6及API/Analyzer/Ops rc4的实际image ID尚未全部提供，联合入口对这些候选固定占位并在任何客户端或凭据读取前拒绝执行；负责人取回实际构建证据后才更新此pin。

## 一次本地执行

```powershell
python .local/run_stage_sequence.py --stage-ready
```

该标志表示发布负责人已完成以上门禁，不表示脚本自动接受了安全扫描。入口先核对rc6冻结SHA、105源文件、14个bundle及Web、API、Analyzer、Ops与平台精确image pin；不接受rc5/rc4或其它旧Web freeze回退。源/包不符、image pin未冻结、公共proof过大/重复键/额外seed字段或旧证据均拒绝。准备时已实际执行只读source preflight并确认全部文件匹配；实际stage浏览器尚未执行。

执行按以下顺序串行进行，第一处失败即停止，不重试后续步骤：

| 顺序 | 实际执行 | 通过条件 |
|---|---|---|
| 1 | `browser_ce_gzip.mjs --proof <固定公共seed>` | 固定stage凭据新登录；fresh grant签identity、路径与当前attempt/artifact吻合；浏览器fetch保留gzip magic及原size/SHA/identity头；普通任务页出现真实`data-flame-source=gzip-artifact` Worker标记。 |
| 2 | `run_stage_browser_rc2.py --stage-ready --suite all` | Full/System合计12项、零failed/skipped/flaky/did-not-run，实际rc6/source/bundle绑定。包含真实PROC_CPU和8类**导入**RAW流水线、授权下载、分享/CSRF、丢响应幂等、取消重试、Cron创建/DAG完成及路由可访问性；不把导入文件改称现场采样。 |
| 3 | `run_stage_flame_rc2.py --stage-ready` | 经真实stage API/Analyzer及浏览器的显式synthetic导入10k/50k/100k；原全部节点/权重与交互断言通过。需步骤2成功创建stage会话状态；保留并恢复非stage旧checkpoint。 |

旧执行器文件名的rc2只是历史名称；实际摘要必须写`web_os_candidate=20261018.1-rc6`并匹配最新freeze，不能把旧报告重标。联合入口不读取凭据，只让已审阅子执行器从固定stage文件读取。清除DEBUG/PWDEBUG/NODE_OPTIONS等调试输入；只输出成功/完成步骤数/固定错误码。子进程原stdout/stderr不回显；不记录网络trace/HAR/video。完整spec使用`--trace off --reporter line --workers 1 --retries 0`，仍保留已审阅的界面截图/axe及脱敏line报告。

每次实际执行新建`docs/evidence/full/stage-sequence-<UTC>-<id>/`，保存各步安全JSON和最终sequence.json；失败记录只含固定code与已完成步骤，不把fixture未执行或skip算通过。原执行器仍有其独立line/截图/性能证据，联合结果通过SHA绑定freeze，不替代逐项报告。

## 准备证据与范围

`stage-browser-sequence-rc6-prepared-2026-10-18.json`绑定rc6联合入口、纯测试和CE浏览器脚本指纹。10项纯导入metadata/假subprocess回归实际通过，包括Web/API/Analyzer/Ops任一未冻结image pin拒绝及旧rc3候选拒绝、隔离项目/新鲜度/image pin、seed类型/额外键、CE精确SHA与Worker、12项零skip、错误脱敏、CRLF二进制读取/大小/重复JSON、遇错停止及显式gate；Ruff/格式/Python编译及Node语法通过。所有写入使用TemporaryDirectory；测试使用明确synthetic image ID，不修改执行器内的待验pin。旧rc4/rc5准备证据保留原文件，不重标。rc6本机131单测/28浏览器fixture与1项默认视觉补充证据与实际stage12项联调属于不同测试范围。

此序列不完成helper所有原生采样器的权限验收、不证明Cron真实到点/DST跳时、不代替内部TLS轮换后的PERF实采、公网HTTPS/Secure Cookie、生产SSE迁移/备份、全链监控或ARM运行。100k是明确synthetic输入的分项基线，不是业务SLA。公网仍是旧版，直到发布负责人另行切换并留证。

部分分析的本机fixture回归已包含取消、重试/重新分析、旧run及旧成功缓存拒绝，但上述12项真实stage规范尚未覆盖真实故障注入。完整部分分析验收仍须另行证明Analyzer在当前租约内提交检查点、后阶段实际失败、API当前run授权返回及浏览器保持FAILED展示，不能仅通过SQL seed补齐此链路。
