"""Generate checked-in Go bindings and the private Python diagnosis binding."""

from __future__ import annotations

import shutil
from pathlib import Path

import grpc_tools
from grpc_tools import protoc


ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parent
GO_OUTPUT = REPOSITORY / "apiserver" / "internal" / "gen" / "mini_drop"
PYTHON_OUTPUT = REPOSITORY / "server" / "app" / "generated"
GO_PROTO_FILES = (
    "common.proto",
    "errorcode.proto",
    "taskkind.proto",
    "hotmethod.proto",
    "healthcheck.proto",
    "init.proto",
    "control.proto",
    "diagnostic_ai.proto",
)


def _plugin(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(
            f"{name} is required; install the matching Go protobuf plugin"
        )
    return executable


def _generate_go() -> int:
    GO_OUTPUT.mkdir(parents=True, exist_ok=True)
    bundled_include = Path(grpc_tools.__file__).resolve().parent / "_proto"
    arguments = [
        "grpc_tools.protoc",
        f"-I{ROOT}",
        f"-I{bundled_include}",
        f"--plugin=protoc-gen-go={_plugin('protoc-gen-go')}",
        f"--plugin=protoc-gen-go-grpc={_plugin('protoc-gen-go-grpc')}",
        f"--go_out={GO_OUTPUT}",
        "--go_opt=paths=source_relative",
        f"--go-grpc_out={GO_OUTPUT}",
        "--go-grpc_opt=paths=source_relative",
        *(str(ROOT / name) for name in GO_PROTO_FILES),
    ]
    return protoc.main(arguments)


def _generate_python_diagnosis() -> int:
    PYTHON_OUTPUT.mkdir(parents=True, exist_ok=True)
    result = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{ROOT}",
            f"--python_out={PYTHON_OUTPUT}",
            f"--grpc_python_out={PYTHON_OUTPUT}",
            str(ROOT / "diagnostic_ai.proto"),
        ]
    )
    if result != 0:
        return result
    grpc_stub = PYTHON_OUTPUT / "diagnostic_ai_pb2_grpc.py"
    generated = grpc_stub.read_text(encoding="utf-8")
    generated = generated.replace(
        "import diagnostic_ai_pb2 as diagnostic__ai__pb2",
        "from . import diagnostic_ai_pb2 as diagnostic__ai__pb2",
    )
    # Keep checked-in generated code stable across Windows and Linux.
    grpc_stub.write_text(generated, encoding="utf-8", newline="\n")
    return 0


def main() -> int:
    go_result = _generate_go()
    if go_result != 0:
        return go_result
    python_result = _generate_python_diagnosis()
    if python_result != 0:
        return python_result
    print(f"Go protobuf bindings generated in {GO_OUTPUT}")
    print(f"Python diagnosis binding generated in {PYTHON_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
