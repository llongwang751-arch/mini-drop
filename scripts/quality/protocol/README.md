# 协议、状态机与真实采样分析门禁

本目录针对测试开发岗位补充未知输入、生命周期组合与持续防退化验证。合成输入明确标注为 synthetic；只有 `run-live-chain.sh` 的 RAW 来自本次真实 C++ PROC_CPU Runner。模型不参与普通 PR 门禁。

## 确定性和随机序列

Go 新增三个原生 Fuzz 入口：`FuzzTaskNormalizeAndValidate`、`FuzzDiagnosisObservationObject`、`FuzzDiagnosisExactScalar`。当前CI每个目标使用单worker、`-fuzztime=200000x`固定执行次数和120秒进程硬超时，保留全部断言且失败立即退出。此前10秒预算轮出现过末时`context deadline exceeded`；[Go官方缺陷82362](https://github.com/golang/go/issues/82362)记录了相符的预算竞态并建议Nx，但不能证明本次就是同一竞态，也不能由缺少失败输入日志推断没有崩溃。原轮次仍保留为失败，不自动重试挑绿。

`capture_fuzz_corpus.py`在CI的always步骤保留上述三个固定`testdata/fuzz/<目标>`中的合成反例及SHA清单。仅接受16或64位hex文件名和Go语料头，拒绝祖先/叶子符号链接、硬链接、非常规文件及读取期间变化；每目标最多8个、单文件384KiB、总量2MiB。超限/拒绝会在清单中失败，不把捕获结果覆盖原Go退出码。空语料不证明没有crash；这些可重放字节来自本轮合成Fuzz，不是业务profile或模型输出。没有发现崩溃也不代表所有输入已验证。

`TestPostgresLifecycleRandomizedProperties` 使用真实 Repository、所有权入口、PostgreSQL 事务及全部迁移，执行 4 个固定种子各 96 个操作。覆盖取消、重试、幂等重放、跨所有者读写、延迟 Outbox 与丢失 ACK；每一步校验 attempt 单调、对象命名空间独立、旧授权失效、事件原子性。失败输出第一个失败操作前缀和 seed；`DROP_LIFECYCLE_SEED=<seed>` 可复现，不把测试自己的转移表当作生产状态机。

Python `test_parser_properties.py` 在固定有界输入内验证树权重守恒、递归 inclusive 去重、重排等价、拆分等价，以及任意字节和损坏尾部永久拒绝。

## 关键风险门禁

先在仓库根目录创建 ignored 的 `.local/quality-protocol`；使用显式隔离测试 DSN，不使用生产连接：

```bash
cd api
go test -json -count=1 -coverprofile=../.local/quality-protocol/repository.cover \
  -run 'TestPostgresLifecycleRandomizedProperties|TestPostgresCancelIsIdempotentForConcurrentAndTerminalRequests|TestPostgresExhaustedDispatchCannotOverwriteConcurrentLifecycle' \
  ./internal/repository > ../.local/quality-protocol/repository-events.jsonl
cd ..
python scripts/quality/protocol/check_risks.py \
  --events .local/quality-protocol/repository-events.jsonl \
  --coverage .local/quality-protocol/repository.cover \
  --output .local/quality-protocol/risk-report.json
```

门禁要求全部指定真实 PG 场景实际通过，跳过、删除或改名都失败。同时检查 `Cancel`、`Retry`、`DispatchOne` 的生产函数语句覆盖，最低值分别 70%、73%、63%。阈值来自 2026-10-23 实际基线 72.0%、75.0%、65.1%，保留小于一条当前语句的精度余量，目的为阻止已有关键路径退化。它既不是分支覆盖率，也不是全仓覆盖率，更不能说明所有业务风险已覆盖。新增生产分支应补对应场景、实跑并审阅阈值，不能只调低数字。

## C++ ASan / UBSan

```bash
docker build --target sanitizer -f core/Dockerfile -t drop-core-sanitizer .
```

独立 `sanitizer` 目标启用 ASan/UBSan、泄漏检查、遇错退出和 `-Wall -Wextra -Wpedantic -Werror`，运行 domain/json/catalog/lifecycle/sampling/protocol_properties 六个 CTest。默认发布镜像不启用消毒器。`protocol_property_test` 调用生产受限 JSON 解析器执行 4096 次固定种子变异、4096 次重复字段拒绝和深度/大小/路径边界。

在已有工具链镜像中也可 `cmake -S core -B <独立目录> -DDROP_ENABLE_SANITIZERS=ON -DCMAKE_BUILD_TYPE=Debug`，构建上述六个 target 后执行同名 CTest。请串行构建、限制 CPU 和内存，不给采集进程额外内核权限。

## 真实 Runner → 存储 → Analyzer

```bash
docker build --target build -f core/Dockerfile -t drop-core-test .
docker build -f analyzer/Dockerfile -t drop-analyzer:build .
# MinIO 使用现有 deploy/minio/Dockerfile 构建的指定版本镜像。
sudo --preserve-env=PATH bash scripts/quality/protocol/run-live-chain.sh
```

脚本需要 Docker 以及创建 UID 10001 私有夹具目录的权限；运行所有容器都使用当前脚本创建的唯一名称和归属标签。支持 `DROP_CORE_TEST_IMAGE`、`ANALYZER_TEST_IMAGE`、`DROP_CORE_BINARIES_DIR`，后者用于把**当前源码新编译**的宿主二进制只读挂载到兼容工具链镜像，而不是偷偷复用旧发布二进制。

采集容器无网络、无 capabilities、只读根目录，限 0.75 CPU / 512 MiB，对同容器 UID 10001 受控程序实采 3 秒。真实 RAW 经 MinIO 上传、Analyzer claim、有资源上限的解析子进程、结果原子提交和下载摘要校验。CPU tick 不解释为函数栈、请求延迟或模型根因。

资源复用既有 `analyzer/scripts/run-isolated-integration.sh` 的无端口隔离网络、tmpfs PostgreSQL/MinIO 与 owned-label 清理。RAW、进程名和私有日志只写 `.local/drop-quality-chain-*/`，公开收据只含二进制/RAW SHA、大小、权重与状态。**不覆盖 Go HTTP 建任务、Agent gRPC、浏览器和付费模型**；这些能力应使用各自独立测试记录，不能把本门禁称为完整平台 E2E。
