"""Render verified load evidence as a self-contained HTML report with plots."""
from __future__ import annotations

import argparse
import base64
from html import escape
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_load_report import verify


def figure(report, samples):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    stages = report["stages"]
    colors = {"PASSED": "#24755b", "INVALID": "#b56b13", "SLO_FAILED": "#b6473d"}
    ax = axes[0, 0]
    bars = ax.bar([s["name"] for s in stages], [s["summary"]["p95_ms"] if s["summary"]["p95_ms"] is not None else float("nan") for s in stages],
                  color=[colors.get(s["summary"]["status"], "#737373") for s in stages])
    ax.bar_label(bars, fmt="%.1f", padding=3)
    ax.axhline(report["plan"]["p95_limit_ms"], color="#b6473d", linestyle="--", label="P95 limit")
    ax.set(title="All stages (including rejected / invalid)", ylabel="P95 ms")
    ax.tick_params(axis="x", rotation=25)
    ax.legend()

    ax = axes[0, 1]
    buckets = stages[-1]["buckets"]
    ax.plot([b["offset_seconds"] / 60 for b in buckets], [b["summary"]["p95_ms"] if b["summary"]["p95_ms"] is not None else float("nan") for b in buckets],
            marker=".", color="#285f9b", label="Arrival-cohort P95")
    ax.axhline(report["plan"]["p95_limit_ms"], color="#b6473d", linestyle="--", label="P95 limit")
    ax.set(title="Continuous-load windows", xlabel="Minutes since soak start", ylabel="P95 ms")
    ax.legend()

    for ax, key, scale, title, ylabel in [
        (axes[1, 0], "rss_bytes", 2 ** 20, "Owned target process RSS", "MiB"),
        (axes[1, 1], "cpu_percent", 1, "Target CPU (100% = one logical core)", "CPU %"),
    ]:
        if samples:
            x, y, previous = [], [], None
            for sample in samples:
                elapsed = sample["elapsed_seconds"]
                if previous is not None and elapsed - previous > 5:
                    x.append(elapsed / 60)
                    y.append(float("nan"))
                x.append(elapsed / 60)
                y.append(sample[key] / scale if sample.get("status") == "OK" and sample.get(key) is not None
                         else float("nan"))
                previous = elapsed
            ax.plot(x, y, color="#285f9b", linewidth=1)
            ax.set(xlabel="Minutes since resource sampler start", ylabel=ylabel)
            ax.set_xlim(left=0)
        else:
            ax.text(.5, .5, "NOT OBSERVED", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title)
        # Absolute resource charts start at zero so tiny normal allocator/CPU
        # changes do not visually resemble a large leak or utilization spike.
        ax.set_ylim(bottom=0)
    for ax in axes.flat:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=.18)
        ax.set_axisbelow(True)
    fig.suptitle("Local fixture evidence — " + report["status"], fontsize=16)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=140)
    plt.close(fig)
    return buffer.getvalue()


def html(report, png):
    def number(value):
        return "未采集" if value is None else f"{value:,.2f}"

    rows = []
    for phase in report["stages"]:
        summary = phase["summary"]
        rows.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in (
            phase["name"], phase["rate"], phase["duration_seconds"],
            f"{summary['offered']} / {summary['sent']} / {summary['unsent']}",
            number(summary["p95_ms"]), number(summary["p99_ms"]),
            f"{summary['success_rate']:.2%} / {summary['quality_rate']:.2%}",
            summary["status"], ", ".join(summary["reasons"]) or "—")) + "</tr>")
    resource = report.get("resources")
    resource_text = "该历史报告未采集目标进程资源，不能补作稳定性结论。"
    if resource:
        resource_text = (f"资源判定 {resource['status']}；持续阶段 {resource['soak_samples']} 个样本；"
                         f"缺失 {resource['error_samples']}；句柄口径 {resource['handle_kind']}。")
    growth = ""
    if resource:
        growth = "<table><thead><tr><th>资源（原始单位）</th><th>首段中位数</th><th>末段中位数</th><th>增长</th><th>预算</th></tr></thead><tbody>"
        for key, item in resource["soak_trends"].items():
            growth += "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in
                (key, number(item["first_third_median"]), number(item["last_third_median"]),
                 number(item["growth"]), number(item["growth_limit"]))) + "</tr>"
        growth += "</tbody></table>"
    status = report["status"]
    theme = "pass" if status == "PASSED" else "fail"
    image = base64.b64encode(png).decode("ascii")
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>负载与资源验收 · {escape(status)}</title>
<style>body{{font:16px/1.65 system-ui,sans-serif;color:#202b34;background:#f5f6f7;margin:0}}main{{max-width:1180px;margin:auto;padding:32px 24px}}h1{{font-size:30px;margin:8px 0}}h2{{font-size:21px;margin-top:32px}}.label{{color:#536573}}.verdict{{padding:18px;border-left:5px solid;background:white}}.pass{{border-color:#24755b}}.fail{{border-color:#b56b13}}img{{width:100%;height:auto;background:white}}table{{border-collapse:collapse;width:100%;font-size:14px;background:white}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #d8dee3;vertical-align:top}}th{{white-space:nowrap}}.scroll{{overflow-x:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px;font-size:12px}}summary{{cursor:pointer}}.note{{color:#536573}}code{{overflow-wrap:anywhere}}</style>
<main><div class="label">Mini-Drop · 测试开发证据报告</div><h1>负载、恢复与资源增长验收</h1>
<div class="verdict {theme}"><strong>{escape(status)}</strong> · 原始证据完整性已复核
<p>只描述本机隔离样例和本次配置，不是生产容量、AI 根因或无泄漏证明。INVALID 表示至少一项测量前提未成立，不能当作通过。</p></div>
<p>CPU、RSS 来自自己创建的目标进程；CPU 100% 表示一个逻辑核。缺失观测不填零。高负载 SLO 失败和发压端无效均保留在下表。</p>
<h2>阶段结果</h2><div class="scroll"><table><thead><tr><th>阶段</th><th>请求/秒</th><th>秒</th><th>计划 / 实发 / 未发</th><th>P95 ms</th><th>P99 ms</th><th>成功 / 引用检查</th><th>判定</th><th>原因</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>延迟与目标进程资源</h2><img alt="各阶段 P95、持续窗口 P95、目标 RSS 和单核口径 CPU 时间序列；精确值见表格和完整报告" src="data:image/png;base64,{image}">
<p>{escape(resource_text)}</p><div class="scroll">{growth}</div>
<p class="note">资源增长为持续阶段末三分之一与首三分之一样本中位数之差。瞬时峰值不等于泄漏；预算以下也不证明所有泄漏不存在。采样空缺在图中保留为断点。</p>
<h2>复现与追溯</h2><p>源报告 SHA-256：<code>{escape(report['sha256'])}</code></p>
<p>原始 JSONL、配置、源码指纹与平台信息一并保留。复核器重算每个阶段和窗口，不以摘要中的 PASSED 字符串作为验收依据。</p>
<details><summary>查看完整机器报告（含配置、边界和指纹）</summary><pre>{escape(json.dumps(report, ensure_ascii=False, indent=2))}</pre></details></main></html>'''


def render(source, output):
    verify(source)
    report = json.loads(source.read_text(encoding="utf-8"))
    samples = []
    if "resources" in report:
        samples = [json.loads(line) for line in (source.parent / "resources.jsonl").read_text(encoding="utf-8").splitlines()]
    document = html(report, figure(report, samples))
    with output.open("x", encoding="utf-8") as stream:
        stream.write(document)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(render(args.report, args.output))
