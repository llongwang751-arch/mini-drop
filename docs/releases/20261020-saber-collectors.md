# Saber 与采集器补齐发布 · 2026-10-20

当前入口：[Drop](https://82.157.81.79)。API `20261020.10`、Web `20261020.10`、Agent/Helper `20261020.3`，SQL8。其余服务保持原命名镜像。保留数据卷、旧镜像和失败记录。

| 本轮范围 | 实际结果 |
|---|---|
| 最终版本完整采集链路 | 16类Runner、20个新建任务62–81全部采集→上传→分析→RAW下载大小/SHA通过；按事件/对象/时序各自口径验收 |
| malloc/free | 任务63，100次分配、100次释放，200条非空用户栈；最终隔离3项验证97/97、9事件预算5/4、取消0.644秒及目标恢复/探针清理 |
| BOLT | 任务81，161个真实cycles样本，9制品逐一校验，优化ELF6组固定输入输出/退出码一致。CPU中位比0.9942，未观察CPU收益；无LBR，不声称分支测量或业务加速 |
| Saber完整网页调查 | 调查9，任务82→83，修复7；88.0秒。真实模型/采样/源码/隔离补丁/原2+冻结6回归，384例一致，6组A/B，中位CPU加速1.3534倍，6/6达门槛，profile/test_patch fresh |
| 工程回归 | C++ Werror及18/18 CTest；完整IPC8/8（明确导入profile）；Go全包+vet及fresh PG110项0skip；执行器4项；12项UI流程fixture+4项BOLT计数fixture、生产构建及真实桌面/手机呈现 |
| 平台其他闭环 | 取消/幂等重试任务50；串行DAG51→52；真实分钟调度任务53触发后停用；5匿名面401；最终同机备份和隔离PG/S3恢复通过 |

Saber接入的是桌面原项目真实FTS5查询组件，使用合成查询和知识块、真实SQLite，原业务代码不自动发布。CPU收益只属于该受控查询基准，不是全Saber或生产P95/P99。重复采样不独立确认根因；源码修改+A/B所支持的结论限定为 `validated_in_controlled_saber_benchmark`。模型解释仍需人工判断。

权限采用三个明确AppArmor角色：capless Agent读取目标身份，Helper执行固定模板，10个受控目标接受指定Helper调试；不修改全局docker-default、不加SYS_ADMIN、不使用privileged/unconfined。Controller不得反向被目标读取。详细隔离正负控见malloc证据。普通完成、预算和请求取消的回收已验证；SIGKILL后的别名/legacy探针残留自动恢复仍有边界，没有重启主机演练。

失败记录包括BOLT43/54、memray61、新目标标签引发的Agent身份读取问题、页故障66的验收器误判，以及最初Saber页面回退显示旧CRM。均保留；66实际采集成功，200fault/0bytes合法。后者通过按ID详情加载修复，Web10又将无分支测量显示为“未测量”，不伪造0。

证据：[最终采集8项](../evidence/malloc-20261020/production-final-regression.json)、[其余12项](../evidence/demo-completion-20261020/final-runners.json)、[Saber本次网页实跑](../evidence/completion-20261020/saber-live-2026-10-20T08-01-13-655Z/summary.json)、[当前就绪](../evidence/completion-20261020/final-readiness.json)、[备份恢复](../evidence/completion-20261020/final-backup.json)。

现场步骤见[面试指南](../INTERVIEW_DEMO.md)，接入范围见[Saber](../SABER_INTEGRATION.md)、[BOLT](../BOLT_DEMO.md)、[malloc](../MALLOC_TRACE_COMPAT.md)。统一镜像漏洞扫描/签名、真实多机/ARM/长期容量、任意业务仓库通用修复不在本次通过结论内，见[边界](../CAPABILITY_GAPS.md)。
