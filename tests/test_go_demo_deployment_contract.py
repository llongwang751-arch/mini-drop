from pathlib import Path
import re


def test_demo_docker_toolchain_satisfies_its_go_module_requirement():
    root = Path(__file__).resolve().parents[1] / "demo/go-hotspot"
    required = re.search(r"^go (\d+)\.(\d+)\.(\d+)$", (root / "go.mod").read_text(), re.MULTILINE)
    builder = re.search(r"^FROM golang:(\d+)\.(\d+)\.(\d+)-alpine AS build$", (root / "Dockerfile").read_text(), re.MULTILINE)
    assert required and builder, "Deployment and module must pin the Go toolchain"
    assert tuple(map(int, builder.groups())) >= tuple(map(int, required.groups()))
