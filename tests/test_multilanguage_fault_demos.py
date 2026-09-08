from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_go_fault_lab_is_idle_bounded_and_allow_listed() -> None:
    source = _source("demo/go-hotspot/main.go")

    assert "var cpuFault faultSwitch" in source
    assert "seconds < 15" in source
    assert "seconds > 300" in source
    assert 'http.HandleFunc("/faults/cpu/start"' in source
    assert 'http.HandleFunc("/faults/network/start"' in source
    assert 'http.HandleFunc("/faults/memory/start"' in source
    assert 'http.HandleFunc("/faults/io/start"' in source
    assert 'http.HandleFunc("/snapshot"' in source
    assert "goCPUHotFunction" in source
    assert "megabytes > 128" in source
    assert 'goIOPath          = "/tmp/mini-drop-go-io-fault.bin"' in source
    assert "file.Sync()" in source


def test_go_pprof_is_reachable_from_the_host_network_interview_agent() -> None:
    compose = _source("docker-compose.control.yml")
    control = _source("native/control/src/main.cpp")
    env_example = _source("deploy/env/control.env.example")

    assert '127.0.0.1:${INTERVIEW_DEMO_GO_PPROF_PORT:-16060}:6060' in compose
    assert "MINI_DROP_GO_PPROF_ENDPOINT" in compose
    assert "MINI_DROP_GO_PPROF_ENDPOINT" in control
    assert 'env_or(\n          "MINI_DROP_GO_PPROF_ENDPOINT"' in control
    assert "INTERVIEW_DEMO_GO_PPROF_PORT=16060" in env_example
    assert (
        "MINI_DROP_GO_PPROF_ENDPOINT=http://127.0.0.1:16060/debug/pprof/profile"
        in env_example
    )


def test_java_fault_lab_is_idle_bounded_and_has_runtime_faults() -> None:
    source = _source("demo/java-hotspot/Hotspot.java")

    assert "new AtomicBoolean(false)" in source
    assert "Math.max(15, Math.min(durationSeconds, 300))" in source
    for route in ("cpu", "gc", "lock", "downstream", "offheap", "io"):
        assert f'createContext("/faults/{route}/start"' in source
        assert f'createContext("/faults/{route}/stop"' in source
    assert "javaCpuHotFunction" in source
    assert "ByteBuffer.allocateDirect" in source
    assert "Math.min(option(payload, \"megabytes\", 96), 128)" in source
    assert 'Path.of("/tmp/mini-drop-java-io-fault.bin")' in source
    assert "channel.force(true)" in source
    assert "-Xmx256m" in _source("demo/java-hotspot/Dockerfile")


def test_cpp_fault_lab_is_idle_bounded_and_caps_memory() -> None:
    source = _source("demo/cpp-hotspot/main.cpp")
    dockerfile = _source("demo/cpp-hotspot/Dockerfile")
    build_script = _source("demo/cpp-hotspot/build.sh")
    analyzer_dockerfile = _source("deploy/dockerfiles/python-worker.Dockerfile")

    assert "std::atomic<bool> active_{false}" in source
    assert "kMinDurationSeconds = 15" in source
    assert "kMaxDurationSeconds = 300" in source
    assert "kMaxMemoryMegabytes = 128" in source
    for route in ("cpu", "lock", "memory", "io", "downstream"):
        assert f'path == "/faults/{route}/start"' in source
        assert f'path == "/faults/{route}/stop"' in source
    assert "cpp_cpu_hot_function" in source
    assert "volatile std::uint64_t value = seed" in source
    assert 'kIoFaultPath[] = "/tmp/mini-drop-cpp-io-fault.bin"' in source
    assert "::fdatasync(file)" in source
    assert "kMaxDownstreamDelayMilliseconds = 1000" in source
    assert '"GET /work HTTP/1.1' in source
    assert "EXPOSE 8084" in dockerfile
    # The final Alpine image intentionally contains no compiler toolchain.
    # Carry the C++ runtime with the binary so the demo cannot build cleanly
    # and then fail only when the interview container starts.
    assert "build.sh /out/cpp-hotspot" in dockerfile
    assert "-static-libgcc -static-libstdc++" in build_script
    assert "-fno-omit-frame-pointer" in build_script
    assert "-rdynamic -no-pie" in build_script
    # The controlled analyzer carries the byte-identical symbol image so
    # perf.data collected across the target container boundary resolves the
    # function name instead of only showing ``[cpp-hotspot]``.
    assert "FROM alpine:3.24.1 AS cpp-demo-symbols" in analyzer_dockerfile
    assert "demo/cpp-hotspot/main.cpp demo/cpp-hotspot/build.sh" in analyzer_dockerfile
    assert "COPY --from=cpp-demo-symbols /out/cpp-hotspot /usr/local/bin/cpp-hotspot" in analyzer_dockerfile


def test_python_fault_lab_defaults_to_idle() -> None:
    source = _source("demo/python-hotspot/app.py")
    assert 'os.getenv("CPU_HOTSPOT_ACTIVE", "0")' in source
    assert "min(max(float(value or 60), 15.0), 300.0)" in source
