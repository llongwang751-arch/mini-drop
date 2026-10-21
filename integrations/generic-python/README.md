# 通用 Python 项目登记与缺陷回归

本适配器让管理员通过 manifest 登记新的 Python 项目，无需在 broker 中增加业务项目分支。它只支持 Python；语言字段和输出协议为未来适配留接口，不代表 Go/Java/C++ 已可通用修复。

`fixture/` 是仓库自有的合成购物车组件，故意保留 SKU 空白边界缺陷及重复计算。它与用户 CRM、Saber 的源码、镜像和历史回执没有关系。原测试与 workload 冻结，新增缺陷用例单独保存。

## 登记边界

管理员离线审阅 `fixture/project.json` 同类模板，并使用 [登记脚本](../../scripts/code_projects.py) 生成不可变快照。命令不联网、不构建镜像，不修改原仓库；目标目录已存在就拒绝覆盖。Linux 生产登记文件必须由 root 拥有、不可组写，镜像使用实际 `sha256:...` ID。

- `schema_version`、`language=python`、`output_contract=drop-python-project-v1` 和 `symbol_mapping=python_cprofile` 固定协议。
- `files` 的完整 SHA 映射生成 `source_revision`，同时登记可读、可改及冻结文件；模型不能修改原测试或 workload。
- `commands.build/test` 是管理员登记的有界 Python argv，最长30秒；不经 shell。`profile/benchmark` 必须调用镜像内可信驱动，项目只提供冻结入口与调用次数。
- `regression_test_dir` 只允许独立新增最多两个 unittest 文件。源码补丁、测试补丁分别校验，模型没有命令、环境变量或任意宿主路径字段。
- 冻结 workload 返回受支持的有界 Python 值（None、str、bool、int、有限 float、list、tuple、dict）。自定义对象、任意第三方类型和跨调用共享的可变对象会被拒绝，需要先适配 workload，而非丢弃类型差异。
- 新协议单独使用 `executor_contract=4`；旧 CRM/Saber 的 preset、作业键、镜像、源码 SHA 和回执保持原协议，不重新解释历史测量。

隔离容器无网络、UID10001、只读快照、无 capability、无业务凭据、无 Docker socket。AST 是额外拒绝边界，不能代替容器隔离。

## 从缺陷到可审阅补丁

修复仍使用 `profile_business_flow`、`read_source`、`test_patch` 三个工具。新项目 `test_patch` 除 `edits` 外必须提供 `regression_tests: [{path, content}]`。

1. 真实 cProfile 返回登记文件的行号、SHA 与开销。
2. 同一组独立新增用例先运行原版本，再运行候选。原版本必须出现真实断言失败；导入错误、语法错误、skip 或只打印 PASS 都不算缺陷复现。
3. 候选必须全部通过新增用例，再执行未修改的原回归；检查输出值、Python 类型和跨调用可变对象隔离。
4. 执行六组交替 CPU 对照，以及相同冻结 baseline 的 sham 负控。测量不足或负控漂移时拒绝性能验收，不以成功启动代替收益证据。
5. 返回 `regression_test_validation`、负控及带 SHA 的 `patch_bundle` 标准 unified diff。交付物可审阅和用于准备 PR，不自动发布原仓库。

模型写出的测试是否表达正确业务需求仍需人工审阅；前后红绿、工具事实和补丁 SHA 不能自动保证测试语义完整。

## 实际验证

本机可信夹具验证不需要 Docker，但**不能作为容器隔离验收**。下面的假镜像 ID 只用于离线快照协议，本地模式不会调用 Docker：

```sh
python integrations/generic-python/verify_fixture.py --output .local/generic-python-local --image-id sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

Linux 沙箱验收必须另行执行。基镜像使用已冻结的 `drop-agent:20261020.1` 缓存，不拉取新版本或重建旧业务镜像；可用 `DROP_PYTHON_BASE` 显式选择另一个已审阅、包含固定 Python 路径的本地镜像。

```sh
docker build --network=none -t drop-generic-python:20261022.1 integrations/generic-python
image_id=$(docker image inspect --format '{{.Id}}' drop-generic-python:20261022.1)
sudo python3 integrations/generic-python/verify_fixture.py --docker --image-id "$image_id" --output /var/tmp/drop-generic-python-acceptance
```

目录必须全新。脚本只登记仓库自有合成夹具并创建本次 job 的容器，不连接模型、生产 API 或业务数据卷；会核对镜像/作业标签后仅移除本次容器。`verification.json` 区分本地子进程和真实 Docker 路径，记录新增测试前后、原回归、负控和输出契约。`runner.private.log` 是私有运行日志，不作为公共证据直接发布。

更新 broker 需要单独替换宿主 `ops/code_executor.py`；通用 runner 是新命名镜像。不得覆盖 CRM/Saber 现有镜像、registry、jobs 或原源码。API 与 broker 的新字段应在同次发布中对齐，根流程的 SQL/HTTP/页面变更另行验收。
