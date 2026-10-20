"""Generate small profiles with actual tools in the isolated fixtures image.

Fixtures are imported test data, not profiles collected from production. The
manifest records executable versions and the SHA-256 of every generated file.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import ctypes
import sys
import re
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "collected"


def run(argv, **kwargs):
    try:
        return subprocess.run(
            list(map(str, argv)), check=True, timeout=120, capture_output=True, **kwargs
        ).stdout.decode(errors="replace")
    except subprocess.CalledProcessError as error:
        print(
            "Fixture tool diagnostic:",
            Path(str(argv[0])).name,
            error.stderr.decode(errors="replace")[-3000:],
            file=sys.stderr,
        )
        raise


def generate_memray():
    import memray

    FIXTURES.mkdir(parents=True, exist_ok=True)
    capture = FIXTURES / "python-memory.bin"
    capture.unlink(missing_ok=True)
    with memray.Tracker(str(capture), memory_interval_ms=1):
        retained = [bytearray(65536) for _ in range(32)]
        time.sleep(0.01)
        del retained[:16]
        time.sleep(0.01)
    del retained
    capture.chmod(0o644)
    return memray.__version__


def generate_java(work):
    java = work / "HeapFixture.java"
    java.write_text("""import com.sun.management.HotSpotDiagnosticMXBean;import java.lang.management.ManagementFactory;import jdk.jfr.Recording;import java.nio.file.Path;import java.time.Duration;
public class HeapFixture {static Object[] retained=new Object[64]; static volatile long sink; static void busy(){long end=System.nanoTime()+700000000;while(System.nanoTime()<end){for(int i=0;i<10000;i++)sink=sink*31+i;}} public static void main(String[] args)throws Exception{Recording recording=new Recording();recording.enable("jdk.ExecutionSample").withPeriod(Duration.ofMillis(5));recording.start();busy();for(int i=0;i<retained.length;i++)retained[i]=new byte[16384];ManagementFactory.getPlatformMXBean(HotSpotDiagnosticMXBean.class).dumpHeap(args[0],true);recording.stop();recording.dump(Path.of(args[1]));recording.close();}}
""")
    run(["javac", java])
    for name in ("java-heap.hprof", "java-cpu.jfr"):
        (FIXTURES / name).unlink(missing_ok=True)
    run(
        [
            "java",
            "-Xms16m",
            "-Xmx48m",
            "-XX:ActiveProcessorCount=1",
            "-cp",
            work,
            "HeapFixture",
            FIXTURES / "java-heap.hprof",
            FIXTURES / "java-cpu.jfr",
        ]
    )
    for name in ("java-heap.hprof", "java-cpu.jfr"):
        (FIXTURES / name).chmod(0o644)


def main():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="real-profile-fixtures-") as temporary:
        work = Path(temporary)
        go = work / "main.go"
        go.write_text("""package main
import("os";"runtime";"runtime/pprof";"time")
var retained [][]byte
func busy(){ x:=uint64(17); deadline:=time.Now().Add(500*time.Millisecond); for time.Now().Before(deadline){for i:=0;i<10000;i++{x=x*1664525+1013904223}}; runtime.KeepAlive(x) }
func main(){cpu,_:=os.Create(os.Args[1]);pprof.StartCPUProfile(cpu);busy();pprof.StopCPUProfile();cpu.Close();for i:=0;i<64;i++{retained=append(retained,make([]byte,65536))};runtime.GC();heap,_:=os.Create(os.Args[2]);pprof.WriteHeapProfile(heap);heap.Close();runtime.KeepAlive(retained)}
""")
        run(["go", "build", "-o", work / "go-fixture", go])
        run(
            [work / "go-fixture", FIXTURES / "go-cpu.pprof", FIXTURES / "go-heap.pprof"]
        )
        generate_java(work)
        import memray

        generate_memray()
        native = work / "native.cc"
        native.write_text("""#include <gperftools/profiler.h>
#include <gperftools/heap-profiler.h>
#include <cstdlib>
#include <cstring>
#include <chrono>
volatile unsigned long sink=17;void busy(){auto end=std::chrono::steady_clock::now()+std::chrono::milliseconds(600);while(std::chrono::steady_clock::now()<end)for(int i=0;i<10000;++i)sink=sink*1664525+1013904223;}
int main(int argc,char**argv){if(argc<3)return 2;if(std::strcmp(argv[1],"cpu")==0){ProfilerStart(argv[2]);busy();ProfilerStop();}else{HeapProfilerStart(argv[2]);void*blocks[64];for(int i=0;i<64;++i){blocks[i]=malloc(32768);memset(blocks[i],i,32768);}for(int i=0;i<16;++i)free(blocks[i]);HeapProfilerDump("fixture");HeapProfilerStop();}return 0;}
""")
        run(
            [
                "g++",
                "-g",
                "-O0",
                "-fno-omit-frame-pointer",
                "-no-pie",
                "-Wl,--emit-relocs",
                native,
                "-lprofiler",
                "-ltcmalloc",
                "-o",
                work / "native-fixture",
            ]
        )
        run([work / "native-fixture", "cpu", FIXTURES / "gperf-cpu.profile"])
        run([work / "native-fixture", "heap", work / "heap"])
        shutil.copyfile(next(work.glob("heap.*.heap")), FIXTURES / "gperf-heap.profile")
        shutil.copyfile(work / "native-fixture", FIXTURES / "native-target.elf")
        jemalloc = work / "jemalloc.c"
        jemalloc.write_text("""#include <jemalloc/jemalloc.h>
#include <stdlib.h>
#include <string.h>
int main(int argc,char**argv){void *blocks[32];for(int i=0;i<32;i++){blocks[i]=malloc(65536);memset(blocks[i],i,65536);}for(int i=0;i<8;i++)free(blocks[i]);const char*path=argv[1];return mallctl("prof.dump",NULL,NULL,(void*)&path,sizeof(path));}
""")
        run(
            [
                "gcc",
                "-g",
                "-O0",
                "-no-pie",
                jemalloc,
                "-ljemalloc",
                "-o",
                work / "jemalloc-fixture",
            ]
        )
        run(
            [work / "jemalloc-fixture", FIXTURES / "jemalloc-heap.profile"],
            env={
                **os.environ,
                "MALLOC_CONF": "prof:true,lg_prof_sample:0,prof_accum:true",
            },
        )
        shutil.copyfile(work / "jemalloc-fixture", FIXTURES / "jemalloc-target.elf")
        # LLVM's instrumentation emits real fdata without requiring host perf.
        bolt = str(
            Path(shutil.which("llvm-bolt") or shutil.which("llvm-bolt-16")).resolve()
        )
        run(
            [
                bolt,
                work / "native-fixture",
                "-instrument",
                "-instrumentation-file=" + str(FIXTURES / "bolt.fdata"),
                "-instrumentation-file-append-pid=0",
                "-o",
                work / "instrumented",
            ]
        )
        run([work / "instrumented", "cpu", work / "instrumented.profile"])
        shutil.copyfile(work / "native-fixture", FIXTURES / "bolt-target.elf")
        # Real process observations in the frozen JSON schema. These explicit
        # self-instrumented fixtures do not claim eBPF/tracefs Runner coverage.
        fixture_file = work / "io.fixture"
        events = []
        descriptor = os.open(fixture_file, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            for index in range(4):
                begin = time.monotonic_ns()
                size = os.write(descriptor, bytes(4096))
                finish = time.monotonic_ns()
                events.append(
                    {
                        "schema_version": 1,
                        "event": "io",
                        "time_ns": finish,
                        "pid": os.getpid(),
                        "operation": "write",
                        "bytes": size,
                        "duration_ns": finish - begin,
                        "path": "/fixture/io.fixture",
                        "comm": "fixture-generator",
                        "event_source": "fixture_self_instrumented_os_write",
                    }
                )
        finally:
            os.close(descriptor)
        (FIXTURES / "io.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events), encoding="utf8"
        )
        libc = ctypes.CDLL(None)
        libc.malloc.argtypes = [ctypes.c_size_t]
        libc.malloc.restype = ctypes.c_void_p
        libc.free.argtypes = [ctypes.c_void_p]
        pointers, events = [], []
        for index in range(4):
            pointer = libc.malloc(16384)
            if not pointer:
                raise RuntimeError("Fixture malloc failed")
            pointers.append(pointer)
            events.append(
                {
                    "schema_version": 1,
                    "event": "alloc",
                    "time_ns": time.monotonic_ns(),
                    "pid": os.getpid(),
                    "address": hex(pointer),
                    "size": 16384,
                    "stack": ["fixture_generator", "libc.malloc"],
                    "event_source": "fixture_self_instrumented_libc",
                }
            )
        for pointer in pointers[:2]:
            libc.free(pointer)
            events.append(
                {
                    "schema_version": 1,
                    "event": "free",
                    "time_ns": time.monotonic_ns(),
                    "pid": os.getpid(),
                    "address": hex(pointer),
                    "event_source": "fixture_self_instrumented_libc",
                }
            )
        (FIXTURES / "memory.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events), encoding="utf8"
        )
        for pointer in pointers[2:]:
            libc.free(pointer)
        statm = list(map(int, Path("/proc/self/statm").read_text().split()))
        snapshot = {
            "schema_version": 1,
            "samples": [
                {
                    "timestamp_ns": time.time_ns(),
                    "rss_bytes": statm[1] * os.sysconf("SC_PAGE_SIZE"),
                    "vms_bytes": statm[0] * os.sysconf("SC_PAGE_SIZE"),
                    "threads": len(list(Path("/proc/self/task").iterdir())),
                    "fd_count": len(list(Path("/proc/self/fd").iterdir())),
                }
            ],
        }
        (FIXTURES / "resources.json").write_text(json.dumps(snapshot))
        begin = time.monotonic()
        uname = run(["uname", "-srm"])
        (FIXTURES / "script.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "template": "uname",
                    "exit_code": 0,
                    "stdout": uname,
                    "stderr": "",
                    "duration_ms": int((time.monotonic() - begin) * 1000),
                }
            )
        )

        def ticks():
            fields = Path("/proc/self/stat").read_text().rsplit(") ", 1)[1].split()
            return int(fields[11]) + int(fields[12])

        before, deadline = ticks(), time.monotonic() + 0.15
        while time.monotonic() < deadline:
            sum(index * index for index in range(1000))
        delta = ticks() - before
        (FIXTURES / "proc.folded").write_text(
            f"fixture-process;thread:{os.getpid()} {delta}\n"
        )
        # Tool-specific CPU captures use isolated toy children and explicit
        # capabilities granted only to the fixture container.
        python_busy = work / "python_busy.py"
        python_busy.write_text(
            "import time\nend=time.monotonic()+3\nwhile time.monotonic()<end:\n sum(i*i for i in range(10000))\n"
        )
        if shutil.which("py-spy"):
            run(
                [
                    "py-spy",
                    "record",
                    "--format",
                    "raw",
                    "--duration",
                    "1",
                    "--rate",
                    "99",
                    "--output",
                    FIXTURES / "python.folded",
                    "--",
                    sys.executable,
                    python_busy,
                ]
            )
        asprof = os.environ.get("ASPROF_EXECUTABLE") or shutil.which("asprof")
        if asprof:
            busy_java = work / "CpuFixture.java"
            busy_java.write_text(
                "public class CpuFixture {static volatile long sink;public static void main(String[]args){long end=System.nanoTime()+5000000000L;while(System.nanoTime()<end)for(int i=0;i<10000;i++)sink=sink*31+i;}}"
            )
            run(["javac", busy_java])
            child = subprocess.Popen(
                ["java", "-Xms16m", "-Xmx32m", "-cp", str(work), "CpuFixture"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                time.sleep(0.3)
                run(
                    [
                        asprof,
                        "-e",
                        "cpu",
                        "-d",
                        "1",
                        "-o",
                        "collapsed",
                        "-f",
                        FIXTURES / "java.folded",
                        str(child.pid),
                    ]
                )
            finally:
                child.terminate()
                child.wait(timeout=5)
        perf_executable = os.environ.get("PERF_EXECUTABLE") or shutil.which("perf")
        if not os.environ.get("PERF_EXECUTABLE"):
            # Ubuntu /usr/bin/perf is a kernel-version wrapper. Locate the
            # actual packaged binary instead of assuming host/container match.
            for candidate in sorted(Path("/usr/lib").glob("linux-tools*/perf")):
                if candidate.is_file():
                    perf_executable = str(candidate.resolve())
                    break
        if perf_executable:
            run(
                [
                    perf_executable,
                    "record",
                    "-q",
                    "-F",
                    "99",
                    "-g",
                    "--call-graph",
                    "fp",
                    "-o",
                    work / "perf.data",
                    "--",
                    work / "native-fixture",
                    "cpu",
                    work / "perf-native.profile",
                ]
            )
            text = run([perf_executable, "script", "-i", work / "perf.data"])
            weights, stack = Counter(), []
            for line in [*text.splitlines(), ""]:
                if not line.strip():
                    if stack:
                        weights[tuple(reversed(stack))] += 1
                        stack = []
                elif line[:1].isspace():
                    match = re.match(r"\s*[0-9a-fA-F]+\s+(.+?)\s+\([^)]*\)\s*$", line)
                    if match:
                        stack.append(match.group(1).replace(";", ":"))
            if not weights:
                raise RuntimeError("Actual perf capture contained no decodable stacks")
            (FIXTURES / "perf.folded").write_text(
                "\n".join(
                    ";".join(frames) + " " + str(count)
                    for frames, count in weights.items()
                )
            )
        versions = {
            "python": run(["python", "--version"]).strip(),
            "go": run(["go", "version"]).strip(),
            "memray": memray.__version__,
            "bolt": run([bolt, "--version"]).strip()[:512],
        }
        versions["jdk"] = run(["java", "--version"]).strip()[:512]
        versions["jfr_tool"] = run(["jfr", "--version"]).strip()
        if perf_executable:
            versions["perf"] = run([perf_executable, "version"]).strip()
            versions["perf_sha256"] = hashlib.sha256(
                Path(perf_executable).read_bytes()
            ).hexdigest()
        if asprof:
            versions["async_profiler"] = run([asprof, "--version"]).strip()
        if shutil.which("py-spy"):
            versions["py_spy"] = run(["py-spy", "--version"]).strip()
        versions["native_libraries"] = run(
            ["dpkg-query", "-W", "libgoogle-perftools-dev", "libjemalloc-dev"]
        ).strip()
        for path in FIXTURES.iterdir():
            if path.is_file():
                path.chmod(0o644)
        manifest = {
            "provenance": "Imported fixtures generated with real tools in an isolated container; no production data.",
            "tools": versions,
            "files": [
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in sorted(FIXTURES.iterdir())
                if path.is_file() and path.name != "manifest.json"
            ],
        }
        (FIXTURES / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf8"
        )
        print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    if "--memray-only" in sys.argv:
        print(generate_memray())
    elif "--java-only" in sys.argv:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="java-fixture-") as directory:
            generate_java(Path(directory))
    else:
        main()
