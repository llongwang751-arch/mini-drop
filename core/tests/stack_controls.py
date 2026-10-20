"""基于真实分配器 RunnerBuilder 程序生成固定的诊断变体，不扩展生产执行能力。"""


def compare_argv(arguments, pid):
    if (not isinstance(arguments, list) or len(arguments) != 6 or
        arguments[:5] != ["/usr/bin/bpftrace", "-q", "-p", str(pid), "-e"] or
        not isinstance(arguments[5], str) or len(arguments[5]) > 65536 or
        arguments[5].count("ustack(perf,16)") != 2):
        raise RuntimeError("Control requires the actual selected-PID allocator Runner argv")
    raw = arguments.copy()
    raw[5] = raw[5].replace("ustack(perf,16)", "ustack(raw,16)")
    return arguments, raw


def stop_gate_argv(arguments, pid):
    selected, _ = compare_argv(arguments, pid)
    source = selected[5]
    if "@drop_done" in source or source.count("{ exit(); }") != 2 or source.count("END { ") != 1:
        raise RuntimeError("Stop-gate control refuses changed exit structure")
    output = []
    probes = 0
    for line in source.splitlines(keepends=True):
        if line.startswith(("uprobe:", "uretprobe:")):
            prefix, rest = line.split(" /", 1)
            predicate, body = rest.split("/", 1)
            line = prefix + " /!@drop_done && (" + predicate + ")/" + body
            probes += 1
        output.append(line)
    if probes != 3:
        raise RuntimeError("Stop-gate control requires exactly three allocator probes")
    program = "".join(output).replace("{ exit(); }", "{ @drop_done=1; exit(); }")
    # bpftrace 排空 END 事件时保持 gate；过早清除可能重新激活
    # 某些工具构建中尚未关闭 link 的 multi-uprobe。
    program = program.replace("END { ", "END { @drop_done=1; ")
    result = selected.copy()
    result[5] = program
    return result
