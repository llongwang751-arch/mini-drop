"""Diagnostic-only instrumentation of the pinned upstream memray GDB script.

Every upstream command remains byte-for-byte, in order. Only fixed diagnostic
echo commands and bounded-call settings are inserted; no user input is GDB code.
This module is never imported by production collection.
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
        # 'commands' starts a command block: an echo inserted immediately after
        # it is executed upon the breakpoint hit, which is intentionally named
        # separately instead of claiming breakpoint installation completed.
        if stage == "BREAKPOINT_COMMANDS":
            output.append("    echo DROP_STAGE_BREAKPOINT_HIT\\n" + newline)
        elif stage:
            output.append(indentation + "echo DROP_STAGE_" + stage + "_END\\n" + newline)
    return "".join(output)


def stage_names(output):
    import re

    return re.findall(r"^DROP_STAGE_[A-Z_]+$", output, flags=re.MULTILINE)[:64]
