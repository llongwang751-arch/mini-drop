# 服务器本机 SBOM 采集

`host_sbom.py` 仅扫描已存在于本机 Docker daemon 的已知 Drop 镜像。可传入六个应用组件及可选的 `drop-nginx` 平台镜像，也可只补未生成结果的组件；每次串行执行，不向开发机传输镜像，不启动容器；Syft内部仍会暂存daemon导出的tar和解包层。候选业务代码和运行产物无需更改。

为提前核查工具依赖，另仅允许固定缓存引用 `drop-core-full-tools-base:v2` 及其预期精确image ID；其他工具标签和registry前缀均拒绝。该报告显式标记 `diagnostic_only=true`、`release_component=false`，不代替最终Agent镜像扫描，也不能占用签名manifest的六应用组件位置。

CLI 必须显式提供一个或多个 `--image REF=sha256:ID`。REF 只接受 `drop-core`、`drop-agent`、`drop-api`、`drop-analyzer`、`drop-web`、`drop-ops` 和可选 `drop-nginx`，最多七项，标签为 `YYYYMMDD.n[-rcN]`；同一组件只允许一次。ID 必须是调用者已核对的完整 SHA256，禁止仅凭可变标签扫描。扫描前使用窄字段inspect读取Id/Os/Architecture/Size，再通过固定Unix socket的image-list API核对同一ID并读取精确整数字节Size；扫描后再次检查同一标签的四个inspect字段完全未变。image-list响应中的Labels等非选定字段不进入记录或日志。

```sh
python3 /opt/drop-build/host_sbom.py \
  --syft /opt/drop-build/tools/syft \
  --image 'drop-web:20261018.1-rc2=sha256:完整的64位已核对镜像指纹'
```

示例占位符不是可执行的真实指纹。实际命令由发布负责人填入准确指纹；不要粘贴 Docker inspect 的完整输出。输出根固定为 `/opt/drop-build/sbom-20261018`，不接受自选路径。以 root 运行；所有祖先目录必须由 root 所有且不可被组或其他用户写入，输出根必须为 0700。工具不修改已有目录权限，拒绝链接、已有结果及重复运行覆盖。输出为每组件 `.cdx.json` 和 `.cdx.record.json`。记录兼容 `api/tools/supplychain.py sign` 的 `reference`、`image_id`、`sbom_file`、`sbom_sha256`，并明确 `source=docker-daemon`；不伪造不存在的 archive SHA。

只接受官方 Syft **1.54.1 Linux amd64** 二进制 SHA256 `dbf75864e7a7ff9e1fbf00552c31483f693188632a9f68f343cd7653dac513d6`。官方发布 archive 的校验值为 `c069905b391cc4c20a5ba65ad5c10be2a7ba074f8ea6ad203e24d14e303dad47`，archive 下载/校验由发布负责人完成，此工具重新检查实际可执行文件。二进制及其祖先目录也必须由 root 所有且不可被其他用户写入。

Syft 使用 `docker:sha256:... --scope squashed --output cyclonedx-json=...`，单 cataloger、单 Go 线程，独立空白 HOME/私有临时目录，不继承 shell 凭据、代理、Docker context 或 Syft 配置。更新检查与远端信息补全关闭。历史384MiB/no-swap/.5CPU扫描在Ops小镜像成功、在工具基镜像实际OOM；准备的新systemd包装为 **MemoryMax=768M、MemorySwapMax=0、CPUQuota=50%、禁止外联**，I/O读取16,000,000 B/s、写入8,000,000 B/s，启动前额外要求宿主MemAvailable至少1.5GiB。新预算是否实际采用以每次独立`.limits.attempt-*.json`为准，准备不等于扫描成功。`PrivateNetwork=yes` 可保留 Unix Docker socket 的访问，但不要隐藏 `/var/run/docker.sock`。Docker daemon 的导出工作不属于扫描子进程的 cgroup；实际限制与资源压力应随扫描记录保存。工具不创建或调度系统服务。

每组件扫描前要求：可用磁盘不少于 **inspect.Size + image-list.Size + 1GiB**、MemAvailable 至少768MiB；memory PSI avg10的some/full不超过1%/0.1%，I/O PSI some/full不超过5%/1%。Docker29.1.3的containerd实现中inspect.Size只含packed内容，image-list.Size还计入unpacked snapshots，因此不能再用2×inspect.Size推算解包预算。新公式保守覆盖packed tar和list总量，并额外保留1GiB；记录新增`disk_size_bytes`保存list精确值，不解析CLI的三位有效数字展示。该预算不包括运行中其他写入或任意额外扫描器缓存，不作为不耗尽磁盘的绝对保证；父级cgroup、额外宿主余量及进程外监测由发布负责人设置。

本地Docker请求固定为`/var/run/docker.sock`上的`GET /v1.52/images/json`，只允许已校验引用的`filters.reference`，不读取DOCKER_HOST、context、代理或远端URL；超时30秒、响应最多64KiB、结果必须恰好1条，ID须等于调用者预期值、Size须为正有界整数。HTTP错误/重定向、冲突长度、重复键、过大或损坏JSON和ID变化均在Syft/临时目录创建前拒绝，错误不回显响应正文。JSON响应内其他字段立即丢弃，Env/Cmd不被请求，Labels不发布。

串行锁使用持久root0600、单链接常规文件`.collector.lock`与Linux非阻塞`flock`；文件存在是正常状态，不能据此判断正在扫描，也不要unlink它。锁FD只传给本次Syft子进程：父进程单独崩溃但Syft仍存活时继续互斥，所有持有FD的进程退出后由内核释放。旧20字节标记文件可保持原inode/内容复用；symlink、hardlink、FIFO、目录和非私有权限一律拒绝，运行中inode被换则保留现场报错。OOM遗留的`.drop-sbom-*`目录不自动认领或删除；若成品与record之间中断，已有结果仍拒绝覆盖，留待独立核对。

临时内容只在本次新建的 0700 目录内保存；清理前复核原目录 inode，绝不递归删除被替换的目录或未知目录。生成文件先限制为常规单链接文件及不超过 32MiB，再检查 CycloneDX 非空组件。官方 Syft 将镜像 Labels 放入 image metadata；工具发布前重建 metadata，只放已核对的引用、镜像 ID 与 Syft 版本，保留 package components/dependencies 原样。最终 SHA 覆盖这份去除镜像元数据的 SBOM。镜像内的软件路径属于包清单，宿主临时路径、Env/Cmd/Labels、原始诊断和原始 stderr 不进入记录或 stdout。失败仅返回稳定错误码。

本机纯测试不需要 Docker、Syft、管理员凭据或服务器：

```sh
cd scripts/release
python -m unittest -v test_host_sbom.py
```

Windows可运行28项纯边界检查，另5项明确要求Linux root。2026-10-18在128MiB/no-swap/.25CPU、无网络的原生Linux服务中实际33/33、0skip，包含并发拒绝、SIGKILL释放、子进程继承锁及旧文件保留，证据`docs/evidence/full/host-sbom-lock-linux-2026-10-18.json`。这些测试未运行Syft/Docker，不能代替真实六镜像扫描、SBOM Schema及签名验收。只有实际输出后才能登记其组件数和摘要。

以上33项是磁盘修复前的独立历史证据。新增10项回归后，Windows本机实际为**43项运行、38通过、5项Linux专属skip、0失败**；覆盖官方containerd大小差异、精确字段投影、输入/HTTP/字节/JSON边界、Unix连接、身份变化及新预算临界值。随后发布负责人在128MiB/no-swap/.25CPU、capless/NNP、无网络的原生Linux服务中实际完成**43/43、0skip，0.713052秒**，两源码SHA与本机一致，证据`docs/evidence/full/host-sbom-disk-size-linux-2026-10-18.json`。该套测试没有运行Docker或Syft，真实下一轮扫描仍需独立证据；不沿用历史33项作为新版全绿结论。

大小语义来源：[Docker29.1.3 inspect实现](https://github.com/moby/moby/blob/docker-v29.1.3/daemon/containerd/image_inspect.go)、[同版本image-list实现](https://github.com/moby/moby/blob/docker-v29.1.3/daemon/containerd/image_list.go)、[官方修复moby#53426](https://github.com/moby/moby/pull/53426)。官方29.8.0才将inspect与list的snapshot计量对齐，本修复支持当前29.1.3，不要求为此次扫描重启或升级宿主Docker。

参考：[官方 CLI](https://oss.anchore.com/docs/reference/syft/cli/)、[官方配置](https://oss.anchore.com/docs/reference/syft/configuration/)、[1.54.1 CycloneDX 编码源码](https://github.com/anchore/syft/blob/v1.54.1/syft/format/common/cyclonedxhelpers/to_format_model.go)。
