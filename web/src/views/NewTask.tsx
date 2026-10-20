import TargetFields, { targetPayload } from "../components/TargetFields";
import GroupScope from "../components/GroupScope";
import { useState, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Cpu,
  FileArrowUp,
  Info,
  Waveform,
} from "@phosphor-icons/react";
import { api, newIdempotencyKey } from "../api";
import type { Agent, List, Task, TaskKind } from "../types";
import { initialOptions, normalizeOptions, encodeImport } from "../task-schema";
import ParameterFields from "../components/ParameterFields";
import { PageHeading, ErrorPanel, Busy } from "../components/ui";

export default function NewTask() {
  const navigate = useNavigate(),
    qc = useQueryClient();
  const [groupScope, setGroupScope] = useState({
    group_id: "",
    visibility: "private",
  });
  const [kind, setKind] = useState("PERF_CPU"),
    [mode, setMode] = useState("collect");
  const [target, setTarget] = useState(""),
    [pid, setPid] = useState(""),
    [targetType, setTargetType] = useState("process");
  const [category, setCategory] = useState("cpu");
  const categoryOf = (id: string) =>
    /HEAP|MEMORY/.test(id)
      ? "memory"
      : /CPU|BOLT|COLLAPSED/.test(id)
        ? "cpu"
        : "resources";
  const [expectedUid, setExpectedUid] = useState("");
  const [priority, setPriority] = useState(0);
  const [container, setContainer] = useState(""),
    [runtime, setRuntime] = useState("docker"),
    [startTime, setStartTime] = useState("");
  const [duration, setDuration] = useState(10),
    [frequency, setFrequency] = useState(99),
    [name, setName] = useState("");
  const [text, setText] = useState(""),
    [file, setFile] = useState<File>(),
    [fileError, setFileError] = useState("");
  const [auxiliary, setAuxiliary] = useState<File>();
  const [format, setFormat] = useState("");
  const [options, setOptions] = useState<
    Record<string, string | number | boolean>
  >({});
  const agents = useQuery({
    queryKey: ["agents", "form"],
    queryFn: ({ signal }) =>
      api<List<Agent>>("/agents?page_size=100", { signal }),
  });
  const kinds = useQuery({
    queryKey: ["kinds"],
    queryFn: ({ signal }) => api<TaskKind[]>("/task-kinds", { signal }),
  });
  const selected = kinds.data?.find((k) => k.id === kind),
    imported = mode === "import" || kind === "IMPORT_COLLAPSED";
  const agent = agents.data?.items.find((a) => a.id === target);
  const submission = useRef<
    { fingerprint: string; key: string; name: string } | undefined
  >(undefined);
  const create = useMutation({
    mutationFn: async () => {
      const normalizedOptions = normalizeOptions(selected, options);
      if (imported && !file && !text.trim())
        throw new Error("请选择采样文件或填写折叠栈数据");
      const fingerprint = JSON.stringify({
        name,
        kind,
        target,
        pid,
        targetType,
        container,
        runtime,
        startTime,
        expectedUid,
        duration,
        frequency,
        priority,
        options,
        text,
        groupScope,
        file: file && [file.name, file.size, file.lastModified],
        auxiliary: auxiliary && [
          auxiliary.name,
          auxiliary.size,
          auxiliary.lastModified,
        ],
        format,
        imported,
      });
      if (submission.current?.fingerprint !== fingerprint)
        submission.current = {
          fingerprint,
          key: newIdempotencyKey(),
          name:
            name.trim() ||
            (selected?.display_name || kind) +
              " · " +
              new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        };
      if (file && file.size + (auxiliary?.size || 0) > 32 * 1024 * 1024)
        throw new Error("原始文件和附件合计不能超过32 MiB");
      const extra = auxiliary
        ? [
            {
              filename: "target.elf",
              content_type: "application/octet-stream",
              format: "elf",
              data_base64: encodeImport(
                new Uint8Array(await auxiliary.arrayBuffer()),
              ),
            },
          ]
        : undefined;
      const upload =
        imported && file
          ? {
              filename: file.name,
              format: format || selected?.input_format,
              auxiliary: extra,
              content_type: file.type || "application/octet-stream",
              data_base64: encodeImport(
                new Uint8Array(await file.arrayBuffer()),
              ),
            }
          : undefined;
      return api<Task>("/tasks", {
        method: "POST",
        timeoutMs: 60000,
        idempotencyKey: submission.current.key,
        body: JSON.stringify({
          name: submission.current.name,
          ...(groupScope.group_id ? groupScope : { visibility: "private" }),
          kind,
          priority,
          target_agent_id: imported ? undefined : target,
          parameters: imported
            ? { options: normalizedOptions }
            : {
                ...targetPayload({
                  agent_id: target,
                  pid,
                  type: targetType,
                  runtime,
                  container_id: container,
                  start_time_ticks: startTime,
                  expected_uid: expectedUid,
                }),
                duration_seconds: duration,
                frequency_hz: frequency,
                options: normalizedOptions,
              },
          ...(upload
            ? { import: upload }
            : kind === "IMPORT_COLLAPSED"
              ? { collapsed_text: text }
              : {}),
        }),
      });
    },
    onSuccess: (t) => {
      qc.invalidateQueries();
      navigate("/tasks/" + t.id);
    },
  });
  function selectKind(k: TaskKind) {
    setKind(k.id);
    if (
      k.supported_targets?.length &&
      !k.supported_targets.includes(targetType)
    )
      setTargetType(k.supported_targets[0]);
    setDuration(k.default_duration_seconds || 10);
    setFrequency(k.default_frequency_hz || 99);
    setOptions(initialOptions(k));
    setFormat(k.input_format || "");
    setAuxiliary(undefined);
    setFile(undefined);
    setText("");
    setFileError("");
    if (k.id === "IMPORT_COLLAPSED") setMode("import");
  }
  return (
    <>
      <Link to="/tasks" className="back-link">
        <ArrowLeft size={15} />
        返回诊断任务
      </Link>
      <PageHeading
        eyebrow="NEW DIAGNOSTIC"
        title="开始一次诊断"
        description="从 CPU 到堆内存，以适合现场的方式采集证据。"
        action={
          <Link className="button secondary" to="/composites">
            编排复合诊断
          </Link>
        }
      />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
        className="create-layout"
      >
        <div className="create-main">
          <section className="panel form-section">
            <div className="form-step">
              <span>01</span>
              <h2>选择诊断方式</h2>
            </div>
            <div className="segmented sampling-mode" aria-label="数据来源">
              {[
                ["collect", "在线采集"],
                ["import", "导入原始文件"],
              ].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  aria-pressed={mode === key}
                  className={mode === key ? "selected" : ""}
                  onClick={() => {
                    setMode(key);
                    if (
                      key === "collect" &&
                      (kind === "IMPORT_COLLAPSED" ||
                        (agent && !agent.capabilities.includes(kind)))
                    ) {
                      const k = kinds.data?.find(
                        (k) =>
                          k.runner !== "import" &&
                          k.id !== "COMPOSITE" &&
                          (!agent || agent.capabilities.includes(k.id)),
                      );
                      if (k) {
                        selectKind(k);
                        setCategory(categoryOf(k.id));
                      }
                    }
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            {kinds.error && <ErrorPanel error={kinds.error} />}
            {agent && !imported && (
              <p className="capability-scope">
                仅显示 {agent.hostname || agent.id} 当前支持的采集方式。
                <button
                  type="button"
                  className="text-button"
                  onClick={() => {
                    setTarget("");
                    setPid("");
                  }}
                >
                  重新选择节点
                </button>
              </p>
            )}
            <div className="kind-categories" aria-label="诊断分类">
              {[
                ["cpu", "CPU 与调用栈"],
                ["memory", "内存与堆"],
                ["resources", "I/O 与资源"],
                ["all", "全部方式"],
              ].map(([key, label]) => (
                <button
                  type="button"
                  key={key}
                  aria-pressed={category === key}
                  onClick={() => setCategory(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="kind-options full-kind-options">
              {kinds.data
                ?.filter(
                  (k) =>
                    k.id !== "COMPOSITE" &&
                    (mode === "import" || k.runner !== "import") &&
                    (mode === "import" ||
                      !agent ||
                      agent.capabilities.includes(k.id)) &&
                    (category === "all" || categoryOf(k.id) === category),
                )
                .map((k) => (
                  <button
                    type="button"
                    key={k.id}
                    className={
                      "kind-option " + (kind === k.id ? "selected" : "")
                    }
                    aria-pressed={kind === k.id}
                    onClick={() => selectKind(k)}
                  >
                    {k.runner === "import" ? (
                      <FileArrowUp size={23} />
                    ) : k.id.includes("CPU") ? (
                      <Waveform size={23} />
                    ) : (
                      <Cpu size={23} />
                    )}
                    <strong>{k.display_name}</strong>
                    <small>
                      {k.input_format || k.analysis_pipeline || k.runner}
                    </small>
                    <span className="radio-indicator">
                      {kind === k.id && <Check size={10} weight="bold" />}
                    </span>
                  </button>
                ))}
            </div>
            <div className="info-line">
              <Info size={16} />
              {selected?.description || "正在加载诊断方式…"}
            </div>
          </section>
          <section className="panel form-section">
            <div className="form-step">
              <span>02</span>
              <h2>{imported ? "导入采样数据" : "选择诊断目标"}</h2>
            </div>
            {imported ? (
              <>
                <label className="field import-dropzone">
                  <FileArrowUp size={27} />
                  <strong>{file ? file.name : "选择原始采样文件"}</strong>
                  <small>支持对应采样器的原生格式，最大 32 MiB。</small>
                  <input
                    type="file"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f && f.size > 32 * 1024 * 1024) {
                        setFileError("文件最大 32 MiB");
                        setFile(undefined);
                        return;
                      }
                      setFileError("");
                      setFile(f);
                      if (f) setName(f.name);
                    }}
                  />
                </label>
                <label className="field">
                  <span>原始文件格式</span>
                  <select
                    value={format || selected?.input_format || ""}
                    onChange={(e) => setFormat(e.target.value)}
                  >
                    {(
                      selected?.input_formats || [
                        selected?.input_format || "folded",
                      ]
                    ).map((f) => (
                      <option key={f} value={f}>
                        {f}
                      </option>
                    ))}
                  </select>
                </label>
                {[
                  "BOLT_PROFILE",
                  "GPERF_CPU",
                  "GPERF_HEAP",
                  "JEPROF_HEAP",
                ].includes(kind) && (
                  <label className="field">
                    <span>
                      符号二进制 ELF
                      {kind === "BOLT_PROFILE" ? "（必需）" : "（可选）"}
                    </span>
                    <input
                      type="file"
                      required={kind === "BOLT_PROFILE"}
                      onChange={(e) => setAuxiliary(e.target.files?.[0])}
                    />
                    <small>
                      用于原生符号解析，与采样文件对应的可执行文件。
                    </small>
                  </label>
                )}
                {kind === "IMPORT_COLLAPSED" && (
                  <label className="field">
                    <span>或粘贴折叠调用栈</span>
                    <textarea
                      className="mono"
                      rows={7}
                      value={text}
                      disabled={!!file}
                      onChange={(e) => setText(e.target.value)}
                      placeholder="process;function;child_function 42"
                      maxLength={2097152}
                    />
                    <small>每行一条调用栈，空格后为正整数权重。</small>
                  </label>
                )}
                {fileError && <p className="field-error">{fileError}</p>}
              </>
            ) : (
              <>
                <TargetFields
                  kind={selected}
                  agents={agents.data?.items || []}
                  value={{
                    agent_id: target,
                    pid,
                    type: targetType,
                    runtime,
                    container_id: container,
                    start_time_ticks: startTime,
                    expected_uid: expectedUid,
                  }}
                  onChange={(v) => {
                    setTarget(v.agent_id);
                    setPid(v.pid);
                    setTargetType(v.type);
                    setRuntime(v.runtime);
                    setContainer(v.container_id);
                    setStartTime(v.start_time_ticks);
                    setExpectedUid(v.expected_uid || "");
                  }}
                />
                {agents.error && (
                  <ErrorPanel
                    error={agents.error}
                    retry={() => agents.refetch()}
                  />
                )}
              </>
            )}
          </section>
          <section className="panel form-section">
            <div className="form-step">
              <span>03</span>
              <h2>诊断参数</h2>
            </div>
            <label className="field">
              <span>任务名称</span>
              <input
                value={name}
                maxLength={100}
                onChange={(e) => setName(e.target.value)}
                placeholder="便于回溯的名称"
              />
            </label>
            {!imported && (
              <label className="field">
                <span>队列优先级</span>
                <select
                  value={priority}
                  onChange={(e) => setPriority(Number(e.target.value))}
                >
                  <option value={-10}>低 · -10</option>
                  <option value={0}>普通 · 0</option>
                  <option value={10}>高 · 10</option>
                </select>
                <small>
                  数值较高的任务优先下发；同一优先级按创建顺序执行。
                </small>
              </label>
            )}
            {!imported && (
              <div className="form-grid">
                <label className="field">
                  <span>采集时长</span>
                  <div className="input-unit">
                    <input
                      type="number"
                      required
                      min="1"
                      max={selected?.max_duration_seconds || 60}
                      value={duration}
                      onChange={(e) => setDuration(Number(e.target.value))}
                    />
                    <span>秒</span>
                  </div>
                </label>
                <label className="field">
                  <span>采样频率</span>
                  <div className="input-unit">
                    <input
                      type="number"
                      required
                      min="1"
                      max={selected?.max_frequency_hz || 199}
                      value={frequency}
                      onChange={(e) => setFrequency(Number(e.target.value))}
                    />
                    <span>Hz</span>
                  </div>
                </label>
              </div>
            )}
            <ParameterFields
              kind={selected}
              value={options}
              onChange={setOptions}
            />
            <GroupScope value={groupScope} onChange={setGroupScope} />
            {create.error && <ErrorPanel error={create.error} />}
            <div className="form-footer">
              <Link className="button secondary" to="/tasks">
                取消
              </Link>
              <button
                className="button primary"
                type="submit"
                disabled={create.isPending || !selected || !!fileError}
              >
                {create.isPending ? (
                  <Busy>提交中</Busy>
                ) : (
                  <>
                    创建诊断
                    <ArrowRight size={16} />
                  </>
                )}
              </button>
            </div>
          </section>
        </div>
        <aside className="create-aside">
          <span className="eyebrow">EVIDENCE FIRST</span>
          <h3>
            让现场数据，
            <br />
            成为诊断依据。
          </h3>
          {[
            "校验资源权限与采样能力",
            imported ? "校验原始文件与格式" : "解析目标、受控采集与上传",
            "按数据类型执行专用分析",
            "展示热点、证据与优化建议",
          ].map((s, i) => (
            <div className="aside-step" key={s}>
              <span className="mono">0{i + 1}</span>
              <p>{s}</p>
            </div>
          ))}
          <div className="aside-tip">
            <Info size={18} />
            <p>
              节点只公布探测通过的采样器。缺少工具或内核权限时，请选择合适节点，或导入已有文件。
            </p>
          </div>
        </aside>
      </form>
    </>
  );
}
