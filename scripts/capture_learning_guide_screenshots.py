#!/usr/bin/env python3
"""Capture current Mini-Drop pages for the canonical learning guide.

The script drives a temporary headless Chrome/Edge session through the Chrome
DevTools Protocol. It never writes the API key to disk or stdout. Set the key
in ``MINI_DROP_SCREENSHOT_API_KEY`` before running it.

This is a read-only tour: it opens pages, drawers, tabs and existing records,
but never starts a fault, creates a diagnosis, approves a tool call, publishes
a Skill or deletes data.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
import websocket


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "assets" / "learning-guide"
DEFAULT_URL = "https://120.24.187.205"


def find_browser() -> Path:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    for command in ("chrome", "msedge", "chromium", "chromium-browser"):
        resolved = shutil.which(command)
        if resolved:
            return Path(resolved)
    raise RuntimeError("未找到 Chrome、Edge 或 Chromium")


class Cdp:
    """Small synchronous Chrome DevTools Protocol client."""

    def __init__(self, ws_url: str) -> None:
        self.socket = websocket.create_connection(ws_url, timeout=20, suppress_origin=True)
        self.sequence = 0

    def close(self) -> None:
        self.socket.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sequence += 1
        request_id = self.sequence
        self.socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            payload = json.loads(self.socket.recv())
            if payload.get("id") != request_id:
                continue
            if "error" in payload:
                raise RuntimeError(f"CDP {method} 失败：{payload['error']}")
            return payload.get("result", {})

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return result.get("result", {}).get("value")


def wait_for_devtools(port: int, timeout_seconds: float = 20) -> str:
    deadline = time.monotonic() + timeout_seconds
    endpoint = f"http://127.0.0.1:{port}/json/list"
    while time.monotonic() < deadline:
        try:
            pages = requests.get(endpoint, timeout=1).json()
            page = next(item for item in pages if item.get("type") == "page")
            return str(page["webSocketDebuggerUrl"])
        except (requests.RequestException, ValueError, StopIteration, KeyError):
            time.sleep(0.2)
    raise RuntimeError("浏览器调试端口未在规定时间内就绪")


def wait_for_page(cdp: Cdp, extra_seconds: float = 2.0) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if cdp.evaluate("document.readyState") == "complete":
            break
        time.sleep(0.2)
    time.sleep(extra_seconds)


def click_text(cdp: Cdp, label: str, *, starts_with: bool = False) -> bool:
    label_json = json.dumps(label, ensure_ascii=False)
    comparator = "text.startsWith(label)" if starts_with else "text === label"
    script = f"""
    (() => {{
      const label = {label_json};
      const nodes = [...document.querySelectorAll(
        'button, a, [role="tab"], .ant-segmented-item, .ant-segmented-item-label'
      )];
      const target = nodes.find((node) => {{
        const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
        return {comparator};
      }});
      if (!target) return false;
      target.click();
      return true;
    }})()
    """
    clicked = bool(cdp.evaluate(script))
    if clicked:
        time.sleep(1.5)
    return clicked


def navigate(cdp: Cdp, url: str) -> None:
    cdp.call("Page.navigate", {"url": url})
    wait_for_page(cdp)
    cdp.evaluate("window.scrollTo(0, 0)")


def capture(cdp: Cdp, output: Path, *, preserve_scroll: bool = False) -> None:
    if not preserve_scroll:
        cdp.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.3)
    result = cdp.call(
        "Page.captureScreenshot",
        {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
    )
    output.write_bytes(base64.b64decode(result["data"]))
    print(output.relative_to(ROOT).as_posix())


def capture_tour(cdp: Cdp, base_url: str, output: Path) -> None:
    navigate(cdp, f"{base_url}/ai-diagnosis")
    capture(cdp, output / "01-ai-diagnosis-workbench.png")

    if click_text(cdp, "诊断案例", starts_with=True):
        capture(cdp, output / "02-diagnosis-case-drawer.png")
        if click_text(cdp, "新建诊断"):
            cdp.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.5)
            capture(cdp, output / "02b-new-diagnosis-composer.png", preserve_scroll=True)
            cdp.evaluate("window.scrollTo(0, 0)")
            click_text(cdp, "诊断案例", starts_with=True)
        selected = cdp.evaluate("""
        (() => {
          const rows = [...document.querySelectorAll('.diagnosis-case-row')];
          const row = rows.find((item) => /已完成|证据不足/.test(item.innerText)) || rows[0];
          const button = row?.querySelector('.diagnosis-case-select');
          if (!button) return false;
          button.click();
          return true;
        })()
        """)
        if selected:
            time.sleep(2)
            capture(cdp, output / "03-selected-diagnosis.png")
            opened_metric = cdp.evaluate("""
            (() => {
              const button = document.querySelector('[aria-label="查看规划假设"]');
              if (!button) return false;
              button.click();
              return true;
            })()
            """)
            if opened_metric:
                time.sleep(1)
                capture(cdp, output / "03b-cockpit-metric-dialog.png")
                cdp.evaluate("document.querySelector('.ant-modal-close')?.click()")
                time.sleep(0.5)
            if click_text(cdp, "探索树"):
                capture(cdp, output / "04-dynamic-exploration-tree.png")

    if click_text(cdp, "验证与 A/B"):
        capture(cdp, output / "05-evaluation-overview.png")
        if click_text(cdp, "故障广场"):
            capture(cdp, output / "06-fault-plaza.png")
        if click_text(cdp, "Skill A/B"):
            capture(cdp, output / "07-skill-ab.png")
        if click_text(cdp, "Skill 示例与沉淀"):
            capture(cdp, output / "08-skill-plaza.png")

    navigate(cdp, f"{base_url}/tasks")
    capture(cdp, output / "09-task-dashboard.png")
    opened_result = any(
        click_text(cdp, label)
        for label in ("查看火焰图", "查看图表", "查看原因", "查看进度")
    )
    if opened_result:
        wait_for_page(cdp, 1)
        capture(cdp, output / "10-task-result.png")

    navigate(cdp, f"{base_url}/schedules")
    capture(cdp, output / "11-schedules.png")
    if click_text(cdp, "新建计划"):
        capture(cdp, output / "11b-schedule-form.png")
    navigate(cdp, f"{base_url}/audit")
    capture(cdp, output / "12-audit-log.png")


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取 Mini-Drop 教学文档截图")
    parser.add_argument("--base-url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--port", type=int, default=9339)
    args = parser.parse_args()

    api_key = os.environ.get("MINI_DROP_SCREENSHOT_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("请通过 MINI_DROP_SCREENSHOT_API_KEY 环境变量提供访问凭据")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    browser = (args.browser or find_browser()).resolve()
    base_url = args.base_url.rstrip("/")

    with tempfile.TemporaryDirectory(prefix="mini-drop-doc-browser-") as profile:
        command = [
            str(browser),
            "--headless=new",
            f"--remote-debugging-port={args.port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "--ignore-certificate-errors",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=1920,1080",
            "about:blank",
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        cdp: Cdp | None = None
        try:
            ws_url = wait_for_devtools(args.port)
            cdp = Cdp(ws_url)
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("Security.setIgnoreCertificateErrors", {"ignore": True})
            cdp.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False},
            )
            init_script = (
                "try { localStorage.setItem('mini-drop-api-key', "
                + json.dumps(api_key)
                + "); } catch (_) {}"
            )
            cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": init_script})
            capture_tour(cdp, base_url, output)
        finally:
            if cdp is not None:
                cdp.close()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
