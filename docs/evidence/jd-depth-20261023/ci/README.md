# CI 历史与证据范围

这些文件保留实际失败，不把修复后的结果写回旧轮次。每份 `history-<run>.json` 绑定该轮功能提交、实际 job 终态、失败位置和私有完整日志的 SHA；完整日志不公开。

| JD workflow run | 功能提交 | 当轮已验证结果与失败 |
| --- | --- | --- |
| [39833268689](https://github.com/llongwang751-arch/mini-drop/actions/runs/39833268689) | d91e6f0 | JD 2/3；API runtime 的 APK 镜像索引引用了不存在的 musl 包，未进入完整浏览器链。 |
| [39835307938](https://github.com/llongwang751-arch/mini-drop/actions/runs/39835307938) | ae1cf02 | JD 2/3；Web 容器测试缺共享 fixture，未进入完整浏览器链。 |
| [39837800056](https://github.com/llongwang751-arch/mini-drop/actions/runs/39837800056) | e0b497a | JD 2/3；Web 生产构建还缺共享 schema，未进入完整浏览器链。 |
| [39840075986](https://github.com/llongwang751-arch/mini-drop/actions/runs/39840075986) | bd3c045 | 同提交基线 6/7、JD 2/3，合计 8/10；契约字段覆盖与首次真实浏览器门禁失败。 |
| [39844103076](https://github.com/llongwang751-arch/mini-drop/actions/runs/39844103076) | 093a05d | 同提交基线 7/7、JD 2/3，合计 9/10；登录与读取鉴权通过，但固定等待内没有 Agent 注册。 |

bd3 的原始 `login` 阶段同时覆盖了登录及 Agent 就绪等待，不能据此称为认证失败。093 增加了安全细分阶段：实际登录成功，最后一次 Agent 列表请求为 HTTP 200，列表仍为零；八个本轮常驻容器均运行且未发生 OOM，清理了十二个本轮容器。这些证据将问题缩小到初始化或注册路径，尚不能独立证明具体根因。

`<run>-summary.json`、`<run>-browser.json` 和构建绑定文件来自固定公开 CI artifact，保持原字节。绑定文件说明实际镜像和静态制品 SHA；编译成功由同轮前置构建步骤证明。它们不包含私有日志、凭据、证书、会话、签名 URL、RAW、截图或模型正文。

`history-manifest.json` 只校验最初保留的历史文件。最终同提交的十个 job 必须重新查询并全部达到终态后才能生成最终 `github-ci.json`；不能挪用另一提交的基线成功。真实服务器的 004 三十分钟运行与 005 版本变更后短验收另见 `../system/`，不代替这里失败的 GitHub 门禁。
