/** Explicitly activated client for the separate loopback teaching API. */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ArrowRight, Drop } from "@phosphor-icons/react";
import "./book.css";
import "./workbench.css";

interface LessonResult {
  summary: {
    source: string;
    unit: string;
    total_ticks: number;
    clock_ticks_per_second: number;
    limitations: string[];
  };
  input_sha256: string;
  flamegraph: unknown;
}
interface LessonTask {
  id: string;
  attempt: number;
  status: "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED";
  iterations: number;
  error_code?: string;
  result?: LessonResult;
  events: { kind: string; attempt: number; at: string }[];
}

export default function LabWorkbench() {
  const [connected, setConnected] = useState(false);
  const [tasks, setTasks] = useState<LessonTask[]>([]);
  const [iterations, setIterations] = useState(1000000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selection, setSelection] = useState("");
  const controllers = useRef(new Set<AbortController>());
  const mounted = useRef(true);
  const pendingCreation = useRef<
    { iterations: number; key: string } | undefined
  >(undefined);
  const pendingRetries = useRef(new Map<string, string>());
  const latestRefresh = useRef(0);
  const active = tasks.some((task) =>
    ["QUEUED", "RUNNING"].includes(task.status),
  );
  const selected = tasks.find((task) => task.id === selection) || tasks[0];

  useEffect(() => {
    mounted.current = true;
    const owned = controllers.current;
    return () => {
      mounted.current = false;
      owned.forEach((controller) => controller.abort());
    };
  }, []);

  async function request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    key?: string,
  ): Promise<T> {
    const controller = new AbortController();
    controllers.current.add(controller);
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(`/mini-api${path}`, {
        method,
        signal: controller.signal,
        headers: {
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...(key ? { "Idempotency-Key": key } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "omit",
        redirect: "error",
      });
      const reader = response.body?.getReader();
      if (!reader) throw new Error("教学 API 没有返回数据。");
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        for (;;) {
          const chunk = await reader.read();
          if (chunk.done) break;
          size += chunk.value.byteLength;
          if (size > 1024 * 1024)
            throw new Error("教学 API 返回内容超出 1 MiB 限额。");
          chunks.push(chunk.value);
        }
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.byteLength;
      }
      let payload: { data: T; error?: { code?: string } };
      try {
        payload = JSON.parse(new TextDecoder().decode(bytes));
      } catch {
        throw new Error(
          "没有收到教学 API JSON，请检查 4330 服务与 Vite 教学代理。",
        );
      }
      if (!response.ok)
        throw new Error(
          `教学 API 拒绝请求：${payload.error?.code || response.status}。`,
        );
      return payload.data;
    } finally {
      clearTimeout(timer);
      controllers.current.delete(controller);
    }
  }

  async function refresh() {
    const sequence = ++latestRefresh.current;
    const items = await request<LessonTask[]>("/tasks");
    if (!Array.isArray(items)) throw new Error("教学 API 任务列表格式不匹配。");
    if (mounted.current && sequence === latestRefresh.current) {
      setTasks(items);
      setConnected(true);
    }
  }

  useEffect(() => {
    if (!connected || !active) return;
    let reading = false;
    const timer = setInterval(() => {
      if (reading) return;
      reading = true;
      void refresh()
        .catch(() => {
          if (mounted.current)
            setError("读取任务进度失败，可点击刷新重新连接。");
        })
        .finally(() => {
          reading = false;
        });
    }, 800);
    return () => clearInterval(timer);
  }, [connected, active]);

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (failure) {
      if (mounted.current)
        setError(failure instanceof Error ? failure.message : "教学请求失败。");
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  return (
    <div className="drop-book lesson-workbench-shell" data-theme="light">
      <header className="book-header">
        <Link className="book-brand" to="/book/chapter11/">
          <Drop size={27} weight="fill" />
          <span>
            drop.<small>实战工作台</small>
          </span>
        </Link>
        <nav className="book-header-links" aria-label="实战导航">
          <Link to="/book/chapter11/">
            <ArrowLeft size={15} />
            回到实战篇
          </Link>
          <Link to="/book/chapter17/">
            阅读 React 实现
            <ArrowRight size={15} />
          </Link>
        </nav>
      </header>
      <main className="lesson-workbench">
        <p className="book-eyebrow">四种语言，跑通一次实际交接</p>
        <h1>你的第一条采集链</h1>
        <p className="lesson-intro">
          先按第 11 章启动本机 4330 教学 API，再连接。C++ 测量示例进程自身的 CPU
          计数，Go 安排任务，Python 校验并生成结果，这个页面负责读取与展示。
        </p>
        <p className="lesson-boundary">
          这一版使用文件 journal 和单工作线程，测量粒度是受控进程；完整 Drop
          的权限、PostgreSQL 事务、远程 Agent 和函数栈采样在源码精读篇展开。
        </p>
        <div className="lesson-actions">
          <button disabled={busy} onClick={() => void perform(refresh)}>
            {connected ? "刷新任务" : "连接本地服务"}
          </button>
          <span role="status">
            {connected ? `已连接 · ${tasks.length} 项任务` : "尚未连接"}
          </span>
        </div>
        {error && (
          <p className="lesson-error" role="alert">
            {error}
          </p>
        )}
        <section
          className="lesson-create"
          aria-labelledby="lesson-create-title"
        >
          <h2 id="lesson-create-title">创建一项受控采集</h2>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void perform(async () => {
                if (
                  !Number.isInteger(iterations) ||
                  iterations < 1000 ||
                  iterations > 10000000
                )
                  throw new Error("迭代次数应为 1,000–10,000,000 的整数。");
                if (pendingCreation.current?.iterations !== iterations)
                  pendingCreation.current = {
                    iterations,
                    key: `lesson-${crypto.randomUUID()}`,
                  };
                const task = await request<LessonTask>(
                  "/tasks",
                  "POST",
                  { iterations },
                  pendingCreation.current.key,
                );
                pendingCreation.current = undefined;
                if (mounted.current) setSelection(task.id);
                await refresh();
              });
            }}
          >
            <label htmlFor="lesson-iterations">受控计算迭代次数</label>
            <input
              id="lesson-iterations"
              type="number"
              min={1000}
              max={10000000}
              step={1000}
              value={iterations}
              onChange={(event) => setIterations(Number(event.target.value))}
            />
            <button disabled={!connected || busy} type="submit">
              创建采集任务
            </button>
          </form>
          <p>
            请求响应不确定时，再次提交相同参数会沿用该请求的幂等键。主动重试终态任务会建立新的
            attempt。
          </p>
        </section>
        <div className="lesson-columns">
          <section aria-labelledby="lesson-tasks-title">
            <h2 id="lesson-tasks-title">任务与执行轮次</h2>
            {tasks.length === 0 ? (
              <p>连接后创建一项任务，观察 QUEUED、RUNNING 和终态。</p>
            ) : (
              <ul className="lesson-task-list">
                {tasks.map((task) => (
                  <li key={task.id}>
                    <button
                      aria-pressed={selected?.id === task.id}
                      onClick={() => setSelection(task.id)}
                    >
                      <strong>
                        任务 {task.id} · 第 {task.attempt} 轮
                      </strong>
                      <span>{task.status}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section aria-labelledby="lesson-evidence-title">
            <h2 id="lesson-evidence-title">当前证据</h2>
            {selected ? (
              <>
                <p>
                  状态：<strong>{selected.status}</strong> · 迭代次数{" "}
                  {selected.iterations.toLocaleString()}
                </p>
                {selected.error_code && (
                  <p className="lesson-error">错误码：{selected.error_code}</p>
                )}
                {["SUCCESS", "FAILED"].includes(selected.status) && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void perform(async () => {
                        const key =
                          pendingRetries.current.get(selected.id) ||
                          `retry-${crypto.randomUUID()}`;
                        pendingRetries.current.set(selected.id, key);
                        await request(
                          `/tasks/${encodeURIComponent(selected.id)}/retry`,
                          "POST",
                          {},
                          key,
                        );
                        pendingRetries.current.delete(selected.id);
                        await refresh();
                      })
                    }
                  >
                    重试这一轮
                  </button>
                )}
                {selected.result ? (
                  <>
                    <dl>
                      <dt>来源 / 单位</dt>
                      <dd>
                        {selected.result.summary.source} /{" "}
                        {selected.result.summary.unit}
                      </dd>
                      <dt>实际 CPU 计数</dt>
                      <dd>{selected.result.summary.total_ticks}</dd>
                      <dt>每秒时钟计数</dt>
                      <dd>{selected.result.summary.clock_ticks_per_second}</dd>
                      <dt>原始输入 SHA-256</dt>
                      <dd>
                        <code>{selected.result.input_sha256}</code>
                      </dd>
                    </dl>
                    <pre tabIndex={0} aria-label="实际分析结果">
                      {JSON.stringify(selected.result, null, 2)}
                    </pre>
                  </>
                ) : (
                  <p>结果仅在 Python 分析完成后出现。</p>
                )}
                <h3>事件记录</h3>
                <ol className="lesson-events">
                  {selected.events.map((event, index) => (
                    <li key={index}>
                      第 {event.attempt} 轮 · {event.kind}{" "}
                      <time>{event.at}</time>
                    </li>
                  ))}
                </ol>
              </>
            ) : (
              <p>选择一项任务查看它的结果、来源和事件。</p>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
