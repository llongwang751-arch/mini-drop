# CI 历史与证据范围

这些文件保留实际失败，不把修复后的结果写回旧轮次。每份 `history-<run>.json` 绑定该轮功能提交、实际 job 终态、失败位置和私有完整日志的 SHA；完整日志不公开。

| JD workflow run | 功能提交 | 当轮已验证结果与失败 |
| --- | --- | --- |
| [39833268689](https://github.com/llongwang751-arch/mini-drop/actions/runs/39833268689) | d91e6f0 | JD 2/3；API runtime 的 APK 镜像索引引用了不存在的 musl 包，未进入完整浏览器链。 |
| [39835307938](https://github.com/llongwang751-arch/mini-drop/actions/runs/39835307938) | ae1cf02 | JD 2/3；Web 容器测试缺共享 fixture，未进入完整浏览器链。 |
| [39837800056](https://github.com/llongwang751-arch/mini-drop/actions/runs/39837800056) | e0b497a | JD 2/3；Web 生产构建还缺共享 schema，未进入完整浏览器链。 |
| [39840075986](https://github.com/llongwang751-arch/mini-drop/actions/runs/39840075986) | bd3c045 | 同提交基线 6/7、JD 2/3，合计 8/10；契约字段覆盖与首次真实浏览器门禁失败。 |
| [39844103076](https://github.com/llongwang751-arch/mini-drop/actions/runs/39844103076) | 093a05d | 同提交基线 7/7、JD 2/3，合计 9/10；登录与读取鉴权通过，但固定等待内没有 Agent 注册。 |
| [39846360210](https://github.com/llongwang751-arch/mini-drop/actions/runs/39846360210) | 6aeb122 | 同提交基线 7/7、JD 2/3，合计 9/10；初始化已完成，实际 TLS 握手通过，但 gRPC 注册失败二十六次。 |
| [39849366453](https://github.com/llongwang751-arch/mini-drop/actions/runs/39849366453) | 0cc95f6 | 诊断插桩轮，不能作为最终验收；同提交基线 7/7、JD 1/3，合计 8/10。Go Fuzz 在时长预算末尾非零退出；独立的真实浏览器门禁记录 Agent 注册 gRPC 14 共二十六次。 |
| [39854002402](https://github.com/llongwang751-arch/mini-drop/actions/runs/39854002402) | 91aaa00 | 诊断插桩轮，基线 7/7、JD 2/3，合计 9/10。Linux Fuzz 实报 600028 次全部 PASS；原浏览器仍失败，同栈 default→native→default 明确重现解析策略差异。 |

bd3 的原始 `login` 阶段同时覆盖了登录及 Agent 就绪等待，不能据此称为认证失败。093 增加了安全细分阶段：实际登录成功，最后一次 Agent 列表请求为 HTTP 200，列表仍为零；八个本轮常驻容器均运行且未发生 OOM，清理了十二个本轮容器。这些证据将问题缩小到初始化或注册路径，尚不能独立证明具体根因。

6aeb 进一步保留固定事件计数和实际 TLS 探针，确认 Core 已监听、Agent 已完成初始化，排除了单纯构造过慢。openssl 探针的握手与证书链验证成功，不等价于完整排除 C++ gRPC 客户端的全部连接失败。该轮没有具体 gRPC 状态码，仍不能归因为 DNS、注册请求校验或数据库拒绝。API 的 Docker healthcheck 只检查 `/health/live`，不能把 `healthy` 当作 `/health/ready` 已经通过。

`<run>-summary.json`、`<run>-browser.json` 和构建绑定文件来自固定公开 CI artifact，保持原字节。绑定文件说明实际镜像和静态制品 SHA；编译成功由同轮前置构建步骤证明。它们不包含私有日志、凭据、证书、会话、签名 URL、RAW、截图或模型正文。

`history-manifest.json` 只校验最初保留的历史文件。最终同提交的十个 job 必须重新查询并全部达到终态后才能生成最终 `github-ci.json`；不能挪用另一提交的基线成功。真实服务器的 004 三十分钟运行与 005 版本变更后短验收另见 `../system/`，不代替这里失败的 GitHub 门禁。

0cc 的 QA 构建只为注册日志添加固定数字状态码，`acceptance_eligible=false`；该轮确实显式请求了内部 `/health/ready`，返回 200。服务端 INTERNAL 计数为零，尚不足证明具体解析器故障。Go Fuzz 的原始非零退出单独保留；上游停止时机缺陷与现象相符，但未证明是本轮原因，原 CI 没有采集到持久化反例目录，不能说没有崩溃。`local-fixed-fuzz-200k-001.json` 仅证明 Windows 上三个目标各二十万次通过，不能替代 Linux CI。

`resolver-probe-001.json` 保留独立服务器薄运行时诊断的 bootstrap CRLF 前置失败；没有到注册阶段。002 使用相同薄运行时、新目录和明确的四十六文件 SHA，默认解析器实际注册成功，因此没有继续 native 对照，不能由动态库版本差异认定 c-ares 为根因。该诊断没有任务、attempt 或模型调用；十二个 owned 容器清理后容器、卷、网络均为零，生产二十九容器与冻结六十一源码、三份环境来源 SHA 保持。它使用服务器冻结构建及显式工作区文件字节，不声称与 CI 镜像字节相同，也不代替最终普通 CI。

91的原始公开summary/browser/resolver-diagnostic逐字节保存，三腿保持原浏览器失败与诊断不合格标记。原default有27次gRPC14且均为固定DNS类，native新CID约1.05秒注册成功，恢复default新CID45.72秒后仍有15次gRPC14/DNS且未注册；两腿均Running/noOOM/ready200，tasks与attempt均零，严格核验只变解析器环境键。此结果证明该GitHub环境的解析策略影响，未证明具体c-ares版本或安全补丁的因果关系。后续普通修复只对QA Agent显式native，并核实际owned运行配置；不回滚安全包、不改生产Compose、不放宽等待，未插桩完整CI结果需另记。
