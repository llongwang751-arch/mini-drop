# Nginx 层候选修补顺序（已准备、未构建）

2026-10-18完成公开上游只读取证，随后按已确认的rc2包版本与明确授权完成本机recipe/独立context准备；未访问ECS、pull或构建镜像。初版冻结见`web-basepatch-rc3-freeze.json`；平台rc1构建实际在240秒墙钟限制结束且无新tag。随后根代理受限原镜像容器实际取Aliyun v3.24 APKINDEX，HTTPS200/528376字节/约1.8秒/exit0，三recipe仅替换仓库hostname，新Web rc4/平台rc2冻结见`web-mirror-rc4-freeze.json`。网络证明不等于安装或修复证明，旧包/报告未覆盖。

当前仅有历史Web rc1的实际Grype结果，包含zlib1.3.2-r0的High匹配；不能重标为rc2结论。官方DockerHub API现报同版本tag最后更新2026-10-14T19:22:40.411642Z，index digest为`sha256:782bb89768cc87b7f95c18b95baa1af250e5d451f26794f59363540161147c01`，Linux/amd64子manifest为`sha256:906927b10cea77d8cd01cad4dc0bf8f872874ca9ff5ddbf49a0b0b0f3fc436f7`。这里的manifest digest不是Docker image/config ID，不能混用；时间戳也不证明zlib已更新。公开API响应摘要见`nginx-upstream-metadata-2026-10-18.json`。

1. 先获得rc2精确镜像ID的实际SBOM，以同一已记录Grype DB扫描，确认是否仍有该匹配及实际安装的zlib版本。官方Alpine v3.24/main SecDB确将CVE-2026-85091列于zlib1.3.2-r1的secfixes，但这不能证明当前官方镜像已含该包。
2. 优先确认同版本官方镜像是否已有包含修复包的不可变digest。经实际SBOM/扫描验证后，Web两份Dockerfile和平台minio/gateway可分别钉住该官方digest；不自动新增自建平台镜像或换Nginx主次版本。多架构index和amd64子manifest分别记录，不把amd64单架构固定写成已支持ARM。
3. Core已实际只读确认rc2安装zlib1.3.2-r0，现已在两份Dockerfile的最终Nginx stage中加入`USER root`、仅hostname替换的sed与原`apk upgrade --no-cache zlib`，保留原最终`USER 101`。本机仅封装recipe/context，尚未执行apk。只用原Alpine分支仓库，不混入edge/testing；真实构建、最终SBOM和Grype仍须确认实际zlib版本及High是否消失，不能把命令零退出当作修复证明。
4. 重新封装独立Web rc4 context（保留原rc3），只使用已冻结的nginx.conf和dist；完整14文件bundle manifest应保持`fcc5bd717a463c1f7f1df3fdb91bceefa200d130e26389684fd3d4e4f7590ed3`。Dockerfile/context/source manifest会改变，重新记录其指纹；不重编译或重新生成JS，也不覆盖rc1/rc2包与镜像。
5. 串行构建后确认UID101、Nginx配置/静态资源/响应压缩和实际stage授权gzip下载、SHA/size、SSE/响应式验收。对新镜像重做SBOM/Grype/签名，保留旧结果的原范围。BusyBox其他匹配单列，不因zlib修补而宣称所有漏洞清零。
6. minio网关和公网gateway也是独立部署引用，Web的apk层不会修复它们。经明确授权，已准备`deploy/Dockerfile.nginx`及单文件context，供第7个基础设施镜像`drop-nginx:20261018.1-rc2`共用（失败rc1包另存）；不复制业务文件，六应用模块仍独立。Compose/CI与实际构建、扫描由发布负责人推进。

生产JS依赖图另有严格退出码的`web-runtime-dependency-audit-2026-10-18.json`：117prod、零已报告漏洞、锁文件未改；它不检查Nginx/Alpine/zlib/BusyBox。

来源：[DockerHub官方tag API](https://hub.docker.com/v2/repositories/nginxinc/nginx-unprivileged/tags/1.30.6-alpine)、[Alpine官方v3.24/main SecDB](https://secdb.alpinelinux.org/v3.24/main.json)、[Alpine apk官方说明](https://docs.alpinelinux.org/user-handbook/0.1a/Working/apk.html)。前两项通过有界公开HTTP实际取得200，web浏览工具访问失败未算取证成功。
