# JD 质量验证工具包

面向测试开发实习岗位，把测试设计、实际执行、失败复现和防退化门禁连起来。各模块沿用 Drop 的真实实现；合成输入、受控变异、独立人工评审与真实采集分别记录，不能互换成功标签。

| 模块 | 执行入口 | 解决的问题 |
| --- | --- | --- |
| CRM业务测试 | [business/run.py](business/run.py) | 真实联系人接口的边界、异常、权限、租户与操作序列；固定需求断言与变异负控 |
| AI测试计划 | [business/plan_with_agentscope.py](business/plan_with_agentscope.py) | 官方AgentScope/DeepSeek选择有界场景，模型不能更改断言、身份、URL或执行代码 |
| Agent评测 | [agent/run.py](agent/run.py)、[评分](agent/quality.py) | 冻结输入与oracle分离，保留失败分母；客观结构匹配与独立人工语义指标分列 |
| HTTP安全 | [security/run.py](security/run.py) | 真实Go路由和隔离PG的拒绝矩阵，逐次核对领域数据没有副作用 |
| 性能测量 | [performance/run_isolated.py](performance/run_isolated.py)、[心跳](../benchmarks/README.md) | 真实采样扰动、阶梯负载、有界持续运行，以及真实Core注册和心跳 |
| 协议与恢复 | [protocol/README.md](protocol/README.md) | 原生Fuzz、真实PG随机操作、关键函数覆盖门禁、C++消毒器及真实采样分析链 |

## 执行顺序

先跑离线边界与确定性测试，再跑本机隔离数据库，之后串行执行受资源限制的容器实验。真实模型单独授权配置与预算；普通PR不调用付费模型或生产地址。

```powershell
python -m unittest discover -s scripts/quality/business/tests -v
python -m unittest discover -s scripts/quality/agent -v
python -m unittest discover -s scripts/quality/security -v
python -m unittest discover -s scripts/quality/performance -v
python -m unittest discover -s scripts/quality/protocol -v
```

原生SDK测试需要先按 `agent_runtime/requirements.lock` 安装带哈希的依赖。缺SDK时的明确skip不能算真实SDK回归通过；新增CI使用完整锁依赖环境执行。真实CRM测试还需要该CRM的实际依赖和显式源码路径，CI不复制外部业务源码。

```powershell
python scripts/quality/business/run.py --crm-source E:/crm/AiNativeCrm-python --output .local/jd-quality/crm-run-001
```

模型可先生成 `plan.json`，再把同一文件交给业务执行器。执行器不会再调用模型或让模型修改预期结果。原源码的前后摘要、固定断言、实际请求计数、sham和受控变异结果均进入独立收据。

Linux采样实验只能使用本工具新建的目标和容器，不接受业务PID或任意远端URL：

```bash
python3 scripts/quality/performance/run_isolated.py \
  --image drop-agent:20261020.3 \
  --output /opt/drop-quality/new-perturbation-run
```

每个输出路径必须全新。发生超时或清理异常也保存失败，不以重跑成功覆盖原收据。日志和现场profile只保存在受控私有目录；公开交付只放经过校验的摘要、计数和明确合成的数据。

## CI与证据

[quality-assurance.yml](../../.github/workflows/quality-assurance.yml)增加三组门禁：SDK/工具回归，真实PG/安全矩阵/Fuzz/覆盖门禁，以及ASan/UBSan/真实Runner→MinIO→Analyzer。最后一组覆盖采集和分析数据链，不代替Go HTTP创建、Agent gRPC派发或浏览器完整E2E。

岗位映射、实际结果及未覆盖项见 [质量实验说明](../../docs/JD_QUALITY_LAB.md)。不得把短时负载、单个合成函数或作者可见回归集宣传成生产容量、长期稳定性或独立语义准确率。
