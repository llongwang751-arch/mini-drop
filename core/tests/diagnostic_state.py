"""Bounded process metadata for owned-toy diagnostics; no addresses or stacks."""
import os
from pathlib import Path
import re
import resource


def identity(pid):
    root = Path("/proc") / str(pid)
    fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
    status = (root / "status").read_text().splitlines()
    executable = (root / "exe").stat()
    return {"pid":pid, "uid":int(next(x.split(":",1)[1].split()[0] for x in status if x.startswith("Uid:"))),
            "start_time_ticks":int(fields[19]), "pid_namespace":os.readlink(root / "ns/pid"),
            "namespace_pid":int(next(x.split(":",1)[1].split()[-1] for x in status if x.startswith("NSpid:"))),
            "executable_device":executable.st_dev, "executable_inode":executable.st_ino}


def limits():
    os.setsid()
    os.umask(0o077)
    for name, maximum in ((resource.RLIMIT_CORE,0), (resource.RLIMIT_FSIZE,268435456),
                          (resource.RLIMIT_NOFILE,64), (resource.RLIMIT_AS,8589934592),
                          (resource.RLIMIT_CPU,120), (resource.RLIMIT_NPROC,32)):
        resource.setrlimit(name, (maximum, maximum))


def inspect_waits(expected):
    pid = expected["pid"]
    try:
        if identity(pid) != expected:
            return {"identity_changed":True}
        root = Path("/proc") / str(pid)
        threads = sorted((item for item in (root / "task").iterdir() if item.name.isdecimal()),key=lambda item:int(item.name))
        result = {"thread_count":len(threads), "threads":[]}
        for thread in threads[:32]:
            value = {"tid":int(thread.name)}
            try:
                status = dict(line.split(":",1) for line in (thread / "status").read_text().splitlines() if ":" in line)
                value["state"] = status["State"].strip().split()[0]
                value["tracer_pid"] = int(status["TracerPid"].strip())
                value["signal_masks"] = {key:status[key].strip() for key in ("SigPnd","ShdPnd","SigBlk")
                                         if re.fullmatch(r"[a-fA-F0-9]{1,16}",status[key].strip())}
                channel = (thread / "wchan").read_text().strip()
                if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}",channel):
                    value["wait_channel"] = channel
                call = (thread / "syscall").read_text().split(maxsplit=1)[0]
                if re.fullmatch(r"-?[0-9]{1,6}",call):
                    value["syscall_number"] = int(call)
                elif call == "running":
                    value["syscall_state"] = call
            except (OSError,IndexError,KeyError,ValueError) as error:
                value["read_error"] = type(error).__name__
            result["threads"].append(value)
        if identity(pid) != expected:
            return {"identity_changed":True}
        return result
    except (OSError,IndexError,KeyError,ValueError) as error:
        return {"read_error":type(error).__name__}
