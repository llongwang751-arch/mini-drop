"""Generate the private diagnosis worker Python gRPC bindings."""

from pathlib import Path

from grpc_tools import protoc


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parent / "server" / "app" / "generated"


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    result = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{ROOT}",
            f"--python_out={OUTPUT}",
            f"--grpc_python_out={OUTPUT}",
            str(ROOT / "diagnostic_ai.proto"),
        ]
    )
    if result != 0:
        return result
    grpc_stub = OUTPUT / "diagnostic_ai_pb2_grpc.py"
    generated = grpc_stub.read_text(encoding="utf-8")
    generated = generated.replace(
        "import diagnostic_ai_pb2 as diagnostic__ai__pb2",
        "from . import diagnostic_ai_pb2 as diagnostic__ai__pb2",
    )
    grpc_stub.write_text(generated, encoding="utf-8")
    print(f"diagnostic_ai Python stub generated in {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
