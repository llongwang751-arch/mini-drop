# Mini-Drop 兼容 TLinux 2、3、4：含义、实现与验收

## 1. 导师所说的“兼容”是什么意思

不是页面能打开，也不是 Docker 镜像能启动就算兼容。对 Mini-Drop 而言，至少包含四层：

1. **能安装**：识别 TLinux 2/3/4、`yum`/`dnf`、x86_64/aarch64 和 Python 版本。
2. **Agent 能上线**：systemd 启动、每 5 秒心跳、把发行版/内核/架构上报给 Server。
3. **采集器不误报**：本机没有 `perf`、`bpftrace`、`py-spy` 或 tracefs 时，不把对应能力上报给 Server；调度器不会把不可能完成的任务发到该节点。
4. **内核能力可验证**：分别检查 procfs、tracefs、BTF、perf/eBPF 权限；能力缺失时明确降级并记录 reason。

## 2. 三代系统的关键差异

| 系统 | 兼容基础 | 常见内核 | 包管理器 | Mini-Drop 关注点 |
|---|---|---:|---|---|
| TLinux 2 | CentOS 7 兼容 | 5.4（历史镜像也可能更老） | yum | 老用户态/依赖版本、工具包可得性 |
| TLinux 3 | CentOS 8 兼容 | 5.4 | dnf/yum | perf/bpftrace 与目标内核匹配 |
| TLinux 4 | 腾讯独立演进 | 6.6 LTS | dnf | 新内核 tracepoint/BTF、aarch64 组合 |

容器只能统一用户态依赖，`perf`、eBPF、tracefs 仍使用宿主机内核，所以仍必须做宿主预检。

## 3. 当前实现

- `agent/mini_drop_agent/platform_compat.py`：识别发行版、版本、内核、架构、包管理器和采集器真实能力。
- Agent 注册时只上报本机真正具备的采集器，缺失原因写入 `os_info`。
- `scripts/check_tlinux_compat.py`：安装前和上线后都可运行的 JSON 预检。
- `deploy/scripts/install-worker.sh`：支持 `MINI_DROP_PYTHON`，验证 Python >=3.9，并在安装后执行预检。
- eBPF 错误提示不再只写 Ubuntu 的 apt，而是区分 TLinux 2 与 TLinux 3/4。

## 4. 每台 TLinux Worker 的验收步骤

```bash
cd /opt/mini-drop
python3 scripts/check_tlinux_compat.py
```

要求 CPU 火焰图时：

```bash
python3 scripts/check_tlinux_compat.py --require sys_metrics --require perf_cpu
```

要求 eBPF I/O 时：

```bash
python3 scripts/check_tlinux_compat.py --require sys_metrics --require ebpf_io
```

然后安装并启动：

```bash
sudo MINI_DROP_PYTHON=/usr/bin/python3 deploy/scripts/install-worker.sh /opt/mini-drop
sudo systemctl enable --now mini-drop-agent
sudo systemctl status mini-drop-agent --no-pager
journalctl -u mini-drop-agent -n 100 --no-pager
```

Web 的 Agent 列表必须满足：

- 节点显示 ONLINE；
- `os_info` 能区分 TLinux 版本、内核和架构；
- 能力列表与预检输出一致；
- 缺少 bpftrace 的节点不显示 `ebpf_io`，而不是接到任务后才失败。

## 5. 兼容结论的边界

单元测试验证的是识别和调度逻辑。最终“已兼容”还需在 TLinux 2、3、4 各一台真实机器上分别保存：预检 JSON、Agent 心跳、perf 任务、eBPF 任务（具备权限时）及失败 reason。没有真机证据的组合应标记为“代码已适配，待真机认证”。
