# gperftools 用户态桥接器

这是 `perf` 权限不可用时的 C/C++ 应用显式接入方案。应用必须由自己的
启动配置加载 `libprofiler` 并设置 `CPUPROFILE`、`CPUPROFILESIGNAL`；桥接器
只以同一 Unix 用户发送信号并复制生成的 profile，不使用 `ptrace`、
`perf_event_open`、`sudo` 或 capabilities。

它会随 `native-agent.Dockerfile` 一起编译到 `/usr/local/bin`，也可以独立构建：

```bash
cmake -S native/gperftools_bridge -B build/gperftools-bridge -DCMAKE_BUILD_TYPE=Release
cmake --build build/gperftools-bridge --parallel
```

```bash
export CPUPROFILE=/tmp/my-service.prof
export CPUPROFILESIGNAL=12
LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libprofiler.so ./my-service

mini-drop-gperftools-bridge \
  --pid "$PID" --profile /tmp/my-service.prof --signal 12 \
  --wait 30 --output /tmp/mini-drop/my-service.prof
```

默认拒绝 root，并要求目标进程与桥接器属于同一 UID。`--allow-root` 只用于
受控兼容测试，不应写入生产服务配置。该路线需要应用预先 opt-in，不能作为
任意进程的透明替代；不满足条件时 Agent 必须降级到系统指标而不是宣称已采样。
