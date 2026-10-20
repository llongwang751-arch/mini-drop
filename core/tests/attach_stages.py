"""仅为诊断固定版本的上游 memray GDB 脚本添加观测点。

上游每条命令的字节与顺序保持不变，只插入固定 echo 和有界调用设置，不把用户输入作为 GDB 代码。
生产采集不会导入此模块。
"""


ATTACH_COMMANDS = {
    "call (int)Py_AddPendingCall(&PyCallable_Check, (void*)0)": "PENDING_CALL",
    "set $result = (int)access($libpath, 4)": "ACCESS",
    "b malloc": "BREAKPOINTS",
    "commands 1-10": "BREAKPOINT_COMMANDS",
    "    bt": "BACKTRACE",
    "    disable breakpoints": "DISABLE_BREAKPOINTS",
    "    delete breakpoints": "DELETE_BREAKPOINTS",
    "    call (void*)dlopen($libpath, $rtld_now)": "DLOPEN",
    "    p (char*)dlerror()": "DLERROR",
    '    eval "sharedlibrary %s", $libpath': "INJECTOR_SYMBOLS",
    '    p (int)memray_spawn_client($port) ? "FAILURE" : "SUCCESS"': "SPAWN_CLIENT",
    "continue": "CONTINUE",
}


def instrument_attach(source):
    lines = source.splitlines(keepends=True)
    for command in ATTACH_COMMANDS:
        if sum(line.rstrip("\r\n") == command for line in lines) != 1:
            raise RuntimeError("Pinned upstream attach stage is absent or duplicated")
    if "DROP_STAGE_" in source or len(source) > 65536:
        raise RuntimeError("Refuse previously instrumented or oversized attach script")
    newline = "\r\n" if "\r\n" in source else "\n"
    output = [
        "set direct-call-timeout 5" + newline,
        "set unwind-on-timeout on" + newline,
        "echo DROP_STAGE_BOUNDED_CALLS_ENABLED\\n" + newline,
    ]
    for line in lines:
        command = line.rstrip("\r\n")
        stage = ATTACH_COMMANDS.get(command)
        indentation = "    " if command.startswith("    ") else ""
        if stage:
            output.append(indentation + "echo DROP_STAGE_" + stage + "_BEGIN\\n" + newline)
        output.append(line if line.endswith(("\r", "\n")) else line + newline)
        # commands 会开启命令块，紧随其后的 echo 仅在断点命中时执行；
        # 该阶段应单独命名，不能误报为断点安装已经完成。
        if stage == "BREAKPOINT_COMMANDS":
            output.append("    echo DROP_STAGE_BREAKPOINT_HIT\\n" + newline)
        elif stage:
            output.append(indentation + "echo DROP_STAGE_" + stage + "_END\\n" + newline)
    return "".join(output)


def stage_names(output):
    import re

    return re.findall(r"^DROP_STAGE_[A-Z_]+$", output, flags=re.MULTILINE)[:64]
