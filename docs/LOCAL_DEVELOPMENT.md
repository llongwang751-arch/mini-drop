# 本地开发

需要 Python3.11+、Docker Desktop（Linux容器）或Linux Docker Engine、Docker Compose2.24.4+。源码编译使用容器内工具链；前端热更新另需Node和npm。首次完整构建需要下载构建/运行依赖，建议至少6GiB可用内存和足够的镜像空间；小服务器使用已验证的镜像集并串行运行。

```sh
python scripts/dev.py doctor
python scripts/dev.py plan
python scripts/dev.py up
python scripts/dev.py smoke
python scripts/dev.py status
python scripts/dev.py stop
```

Windows PowerShell入口：`powershell -File scripts/dev.ps1 up`。`stop`保留数据卷，重新`up`沿用自己的账号、数据库和对象；不会删除生产数据。启动后访问`http://127.0.0.1:18080`，本地账号文件路径由启动器显示，口令不打印。

默认project为`txdrop-dev-local`，运行目录为`.local/dev/local`。独立project名、卷、数据库身份、S3密钥与mTLS CA均在本地创建。端口仅绑定127.0.0.1；不发布数据库/S3/gRPC、不挂宿主PID或Docker socket，不使用生产环境变量。`smoke`只对该独立环境创建一项真实PROC_CPU任务，等待COLLECTED/SUCCESS；不会生成伪造perf样本。

`--instance feature --port 18081`可创建第二个环境；后续命令带同一个instance。端口和镜像选择保存在该环境的settings.json；显式传入`--image-set`可更新镜像选择，否则沿用配置。`check`解析实际Compose配置并验证独立卷、Secret来源、挂载与监听地址。

前端热更新：

```sh
cd web
npm ci
npm run dev:local
```

默认热更新读取local实例实际端口，界面显示“本地开发”。Vite/API使用代理保持同源，不把存储凭据发送到浏览器。

离线或小服务器可先准备JSON镜像集（core/api/analyzer/web/nginx五个键），执行`python scripts/dev.py up --image-set /absolute/images.json --no-build`。`--bootstrap-image`可选择已缓存且含bash/openssl的引导镜像；默认镜像首次可能需要安装这些工具。镜像必须与当前契约及迁移兼容；使用旧镜像不能宣称当前源码已验证。停止服务不删除镜像、卷、任务或证据。

2026-10-19实际验证：8项启动器回归通过；Linux独立project首次/重复启动和真实PROC_CPU采集到分析成功。Windows侧CLI与隔离逻辑已检查，本机Docker Desktop未启动，没有声称在Windows实跑整栈。

本地启动器回归：`python -m unittest discover -s scripts -p test_dev.py -v`。这些隔离检查不替代真实Docker启动、受控采集与浏览器验收。

`up`在依赖就绪后校验并重载minio/web代理，更新镜像导致后端换地址时重新解析上游。最终Core3镜像集显式覆盖、真实启动与PROC_CPU smoke已在Linux复验成功；另有真实stage API换IP的502负控和刷新后401恢复证据。

Core5补验：实际Linux up与PROC_CPU smoke、认证后HTTP目录和当前源码逐项一致。启动器将真源目录复制到独立instance再只读挂载API，避免缓存程序镜像内置旧目录；umask0077下非root/cap0实际读取通过，未放宽外部宿主挂载。
