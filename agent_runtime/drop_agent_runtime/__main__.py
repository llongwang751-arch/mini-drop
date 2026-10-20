"""python -m drop_agent_runtime 的启动入口；具体协议与服务生命周期由 server 模块负责。"""

from .server import main

main()
