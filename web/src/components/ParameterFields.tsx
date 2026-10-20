import { fieldDefinitions } from "../task-schema";
import type { TaskKind } from "../types";
const labels: Record<string, string> = {
  event: "采样事件",
  call_graph: "调用栈展开方式",
  port: "pprof 服务端口",
  path: "pprof 路径",
  profile_type: "Profile 类型",
  debug: "调试输出级别",
  gc: "采样前执行 GC",
  sample_type: "权重类型",
  include: "包含函数",
  exclude: "排除函数",
  label_filter: "标签筛选（JSON）",
  native: "包含原生调用栈",
  live: "仅导出存活对象",
  format: "导出格式",
  min_duration_us: "最低耗时（微秒）",
  max_events: "事件数量上限",
  stack_depth: "调用栈深度",
  category: "事件类别",
  mode: "采样模式",
  include_binary: "附带符号二进制",
  optimize: "生成 BOLT 优化二进制",
  template_id: "受控脚本模板",
};
export default function ParameterFields({
  kind,
  value,
  onChange,
}: {
  kind?: TaskKind;
  value: Record<string, string | number | boolean>;
  onChange: (value: Record<string, string | number | boolean>) => void;
}) {
  return (
    <div className="form-grid">
      {fieldDefinitions(kind).map(([key, f]) => (
        <label className="field" key={key}>
          <span>
            {f.title || labels[key] || key}
            {f.required && <em>*</em>}
          </span>
          {f.type === "object" ? (
            <textarea
              className="mono"
              rows={3}
              value={String(value[key] ?? "")}
              maxLength={4096}
              placeholder='{"service":"checkout"}'
              onChange={(e) => onChange({ ...value, [key]: e.target.value })}
            />
          ) : f.enum ? (
            <select
              required={f.required}
              value={String(value[key] ?? "")}
              onChange={(e) =>
                onChange({
                  ...value,
                  [key]:
                    f.type === "integer"
                      ? Number(e.target.value)
                      : e.target.value,
                })
              }
            >
              <option value="">使用默认值</option>
              {f.enum.map((v) => (
                <option key={String(v)} value={v}>
                  {String(v)}
                </option>
              ))}
            </select>
          ) : f.type === "boolean" ? (
            <select
              value={String(value[key] ?? false)}
              onChange={(e) =>
                onChange({ ...value, [key]: e.target.value === "true" })
              }
            >
              <option value="false">关闭</option>
              <option value="true">开启</option>
            </select>
          ) : (
            <input
              type={
                ["integer", "number"].includes(f.type || "") ? "number" : "text"
              }
              required={f.required}
              min={f.minimum}
              max={f.maximum}
              step={f.type === "integer" ? 1 : "any"}
              maxLength={f.maxLength || 2048}
              value={String(value[key] ?? "")}
              onChange={(e) =>
                onChange({
                  ...value,
                  [key]: ["integer", "number"].includes(f.type || "")
                    ? Number(e.target.value)
                    : e.target.value,
                })
              }
            />
          )}
          {f.description && <small>{f.description}</small>}
        </label>
      ))}
    </div>
  );
}
