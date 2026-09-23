"""Generate the content-free RAG demo fixture used by DEMO_WALKTHROUGH.md."""

import argparse
from pathlib import Path


SENTENCE = (
    "员工年假申请须提前三个工作日提交；审批完成后归档编号为 MD-DEMO-924；"
    "Mini-Drop 用这段文字演示真实分块、Embedding、向量检索和诊断。\n"
)
def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a synthetic UTF-8 RAG demo document")
    parser.add_argument("--chars", type=int, default=20_000)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("mini-drop-demo-20k.txt"))
    args = parser.parse_args()
    if not 1 <= args.chars <= 1_000_000:
        parser.error("--chars must be between 1 and 1,000,000")
    content = (SENTENCE * ((args.chars + len(SENTENCE) - 1) // len(SENTENCE)))[:args.chars]
    target = args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"{target}: {len(content)} characters, {target.stat().st_size} UTF-8 bytes")


if __name__ == "__main__":
    main()
