# Analyzer rc4 安全扫描初步分诊

2026-10-18，尚未完成逐项可达性审计，发布安全门禁未通过。

精确镜像为 `sha256:b9e6b659e0494e5002ed5f49a43812a73c3eb7b966d050b2e325b5990e9af136`。6368组件SBOM SHA256为 `90833691c96f23e121dbb9173f5875f4c4aaddf6b4dab52d82e52209a8a7ed33`；Grype0.120.1及2026-10-17漏洞库的原始报告SHA256为 `ff031e33b19c83a60cf203234cf13d970938a37057b9e23192067d63eee0dbb3`。

实际匹配为57Critical、329High、269Medium、24Low、750Negligible、273Unknown，扫描退出2。没有忽略规则、降阈值或删组件；匹配数不等于独立可利用漏洞数。

| 匹配组 | 实际数量 | 当前判断与下一步 |
|---|---|---|
| Debian linux-perf 6.12.113-1 | 46Critical、231High | 报告使用Debian linux源码包的漏洞条目。必须逐项区分内核代码与用户态perf解析器，核对镜像实际文件/依赖和调用方式；不能把全部条目直接认定误报，也不能把容器镜像版本当宿主内核版本。 |
| libcurl3t64-gnutls 8.14.1-2+deb13u7 | 6Critical、9High | 需逐项核对受影响协议、连接复用/认证前提、实际加载链及离线工具调用范围；当前未做不可达结论。 |
| Debian Python3.13的五个binary包 | 共5Critical，均CVE-2026-19445 | 主worker为Python3.12.15，但系统工具还带有Python3.13。需核对实际工具入口和SSL服务器使用，不能仅凭主worker版本排除。 |

已核对的官方描述示例，只覆盖以下三项：

- [CVE-2026-72398，Debian](https://security-tracker.debian.org/tracker/CVE-2026-72398)：涉及Linux内核SCTP握手的COOKIE-ECHO/INIT校验。当前扫描匹配到linux-perf；该示例说明需要检查源码到binary包的映射，不代表其余45项Critical已核验。
- [CVE-2026-19931，Debian](https://security-tracker.debian.org/tracker/CVE-2026-19931)及[上游curl公告](https://curl.se/docs/CVE-2026-19931.html)：涉及Negotiate认证的HTTP连接复用。Trixie当前版本仍列为受影响，尚未以本系统“不使用该功能”的假定关闭匹配。
- [CVE-2026-19445，Debian](https://security-tracker.debian.org/tracker/CVE-2026-19445)：涉及TLS服务器SNI回调切换SSLContext后的生命周期，公告明确TLS客户端不受影响。该描述不能替代本镜像实际加载链和服务器入口核验，也不能由此处理其他Python告警。

原始SBOM及Grype JSON/summary保留在本目录与`security/`。本报告不包含漏洞利用或原始业务profile，不宣称安全通过；生产没有因此切换到该候选。
