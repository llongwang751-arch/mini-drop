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
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import threading
from queue import Queue, Empty
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
        self.socket = websocket.create_connection(ws_url, timeout=5, suppress_origin=True)
        self.sequence = 0
        self.exceptions = []
        self.blocked_mutations = []
        self.pending = {}
        self.lock = threading.Lock()
        self.running = True
        threading.Thread(target=self._receive, daemon=True).start()

    def _send(self, method, params, inbox=None):
        with self.lock:
            self.sequence += 1
            request_id = self.sequence
            if inbox is not None:
                self.pending[request_id] = inbox
            self.socket.send(json.dumps({"id":request_id,"method":method,"params":params}))
        return request_id

    def _receive(self):
        from urllib.parse import urlparse
        while self.running:
            try:
                raw = self.socket.recv()
                if not raw:
                    break
                payload = json.loads(raw)
            except websocket.WebSocketTimeoutException:
                continue
            except (websocket.WebSocketConnectionClosedException, OSError):
                break
            if payload.get("method") == "Runtime.exceptionThrown":
                self.exceptions.append(payload["params"]["exceptionDetails"].get("text"))
            if payload.get("method") == "Fetch.requestPaused":
                item = payload["params"]
                request = item["request"]
                request_path = urlparse(request["url"]).path
                allowed = request["method"] in ("GET", "HEAD", "OPTIONS") or (
                    request["method"] == "POST" and request_path == "/api/auth/session")
                if not allowed:
                    self.blocked_mutations.append({"method":request["method"],"path":request_path})
                self._send("Fetch.continueRequest" if allowed else "Fetch.failRequest",
                    {"requestId":item["requestId"], **({} if allowed else {"errorReason":"BlockedByClient"})})
            inbox = self.pending.pop(payload.get("id"), None)
            if inbox is not None:
                inbox.put(payload)

    def close(self) -> None:
        self.running = False
        self.socket.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        inbox = Queue()
        request_id = self._send(method, params or {}, inbox)
        try:
            payload = inbox.get(timeout=45)
        except Empty:
            self.pending.pop(request_id, None)
            raise RuntimeError(f"CDP timed out: {method}") from None
        if "error" in payload:
            raise RuntimeError(f"CDP {method} failed: {payload['error']}")
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
        'button, a, summary, [role="tab"], .ant-segmented-item, .ant-segmented-item-label'
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
    inventory = cdp.evaluate("""(() => {
      const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
      return { title: document.title, protocol: location.protocol, url: location.pathname + location.search,
        text: document.body.innerText,
        controls: [...document.querySelectorAll('button, a, input, textarea, select, [role="tab"], summary')]
          .filter(visible).map(e => ({tag:e.tagName.toLowerCase(), type:e.type || '',
            label:e.getAttribute('aria-label') || e.innerText || e.title || '',
            placeholder:e.getAttribute('placeholder') || '', disabled:!!e.disabled})) };
    })()""")
    inventory.update({"captured_at": datetime.now(timezone.utc).isoformat(),
        "screenshot": output.name, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "scope": "LOCAL_DOCUMENT" if inventory.get("protocol") == "file:" else "PUBLIC_READ_ONLY_EXISTING_DATA"})
    output.with_suffix(".json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output.resolve().relative_to(ROOT).as_posix())


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


def capture_deep_tour(cdp: Cdp, base_url: str, output: Path, diagnosis_id: str) -> None:
    from urllib.parse import quote

    def close_dialog():
        cdp.evaluate("[...document.querySelectorAll('.ant-modal-close,.ant-drawer-close')].filter(e=>e.getClientRects().length).at(-1)?.click()")
        time.sleep(0.5)

    navigate(cdp, f"{base_url}/ai-diagnosis")
    if click_text(cdp, "诊断说明"):
        capture(cdp, output / "13-diagnosis-help.png")
        close_dialog()
    cdp.call("Emulation.setDeviceMetricsOverride", {"width":390,"height":844,"deviceScaleFactor":1,"mobile":True})
    capture(cdp, output / "14-mobile-workbench.png")
    cdp.call("Emulation.setDeviceMetricsOverride", {"width":1600,"height":1000,"deviceScaleFactor":1,"mobile":False})
    navigate(cdp, f"{base_url}/ai-diagnosis?case={quote('drop_insight_v2:' + diagnosis_id)}")
    time.sleep(2)
    capture(cdp, output / "03-selected-diagnosis.png")
    for key, selector in [("stage","阶段"),("plan","规划假设"),("rag","知识检索"),("tools","工具"),
                          ("evidence","证据"),("memory","记忆"),("evaluation","工具成功率"),("lats","LATS")]:
        opened = cdp.evaluate("""(() => {
          const fragment = %s;
          const e = [...document.querySelectorAll('.agent-cockpit button')]
            .find(e=>(e.getAttribute('aria-label') || e.innerText).includes(fragment));
          if(!e) return false; e.click(); return true;
        })()""" % json.dumps(selector))
        if opened:
            time.sleep(0.8)
            capture(cdp, output / f"15-cockpit-{key}.png")
            if key == "memory" and click_text(cdp, "跨诊断偏好", starts_with=True):
                capture(cdp, output / "16-operator-memory.png")
            close_dialog()
    if click_text(cdp, "探索树"):
        capture(cdp, output / "04-dynamic-exploration-tree.png")
        if click_text(cdp, "查看搜索预算与评分"):
            capture(cdp, output / "17-tree-budget.png")
        cdp.evaluate("document.querySelector('button[aria-label=\"全屏查看探索树\"]')?.click()")
        time.sleep(0.7)
        capture(cdp, output / "18-tree-fullscreen.png")
        close_dialog()
        if click_text(cdp, "轮次路径"):
            capture(cdp, output / "19-tree-rounds.png")
    click_text(cdp, "对话")
    cdp.evaluate("document.querySelector('.diagnosis-composer')?.scrollIntoView({block:'center'})")
    capture(cdp, output / "20-multiturn-composer.png", preserve_scroll=True)
    click_text(cdp, "验证与 A/B")
    click_text(cdp, "Skill A/B")
    if cdp.evaluate("!!document.querySelector('.skill-experiment-card')"):
        cdp.evaluate("document.querySelector('.skill-experiment-card').scrollIntoView({block:'start'})")
        capture(cdp, output / "21-random-experiment.png", preserve_scroll=True)
    navigate(cdp, f"{base_url}/agent/control-interview-demo-agent")
    capture(cdp, output / "22-agent-detail.png")
    navigate(cdp, f"{base_url}/task/task_20260908_111407_2481eb")
    cdp.evaluate("document.querySelector('iframe')?.scrollIntoView({block:'center'})")
    capture(cdp, output / "23-task-visualization.png", preserve_scroll=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取 Mini-Drop 教学文档截图")
    parser.add_argument("--base-url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--port", type=int, default=9339)
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--deep-only", action="store_true")
    parser.add_argument("--task-only", action="store_true")
    parser.add_argument("--task-id", default="task_20260908_095029_7f7b03")
    parser.add_argument("--diagnosis-id", default="insight_c23e8c442c2343f1bd10cb39bb8666af")
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
            cdp.call("Fetch.enable", {"patterns":[{"urlPattern":"*/api/*", "requestStage":"Request"}]})
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
            if not args.deep_only and not args.task_only:
                capture_tour(cdp, base_url, output)
            if args.deep or args.deep_only:
                capture_deep_tour(cdp, base_url, output, args.diagnosis_id)
            if args.task_only:
                navigate(cdp, f"{base_url}/task/{args.task_id}")
                time.sleep(2)
                capture(cdp, output / "24-python-task-summary.png")
                cdp.evaluate("window.scrollTo(0, 700)")
                capture(cdp, output / "25-python-flamegraph.png", preserve_scroll=True)
            manifest = {"captured_at":datetime.now(timezone.utc).isoformat(), "base_url":base_url,
                "scope":"PUBLIC_READ_ONLY_EXISTING_DATA", "screenshots":sorted(p.name for p in output.glob('*.png')),
                "browser_exceptions":cdp.exceptions, "blocked_mutations":cdp.blocked_mutations}
            (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
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
