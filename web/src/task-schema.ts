/**
 * 依据 /task-kinds 返回的参数 schema 构造表单，而不是在前端拼采集命令行。
 * 这里的校验用于提前反馈输入问题，后端仍须独立验证采集器能力、目标身份和预算。
 */
export interface ParameterField {
  type?: string;
  title?: string;
  description?: string;
  enum?: (string | number)[];
  default?: string | number | boolean;
  minimum?: number;
  maximum?: number;
  maxLength?: number;
  required?: boolean;
  properties?: Record<string, ParameterField>;
  maxProperties?: number;
}
export interface KindSchema {
  parameter_schema?: {
    type?: string;
    properties?: Record<string, ParameterField>;
    required?: string[];
  };
  parameters?: Record<string, ParameterField>;
}
const common = new Set([
  "pid",
  "duration_seconds",
  "frequency_hz",
  "scope",
  "target",
  "budget",
  "options",
]);
// 通用目标/预算由专门组件负责，只把采集器专有 options 展开成动态字段，避免重复输入冲突。
export function fieldDefinitions(
  kind?: KindSchema,
): [string, ParameterField][] {
  const root = kind?.parameter_schema?.properties || kind?.parameters || {};
  const properties = root.options?.properties || root;
  return Object.entries(properties)
    .filter(
      ([key, field]) =>
        !common.has(key) &&
        ["string", "integer", "number", "boolean", "object"].includes(
          field.type || "string",
        ),
    )
    .map(([key, field]) => [
      key,
      {
        ...field,
        type:
          field.type ||
          (typeof field.enum?.[0] === "number" ? "integer" : "string"),
        required:
          field.required || kind?.parameter_schema?.required?.includes(key),
      },
    ]);
}
export function initialOptions(
  kind?: KindSchema,
): Record<string, string | number | boolean> {
  return Object.fromEntries(
    fieldDefinitions(kind)
      .filter(([, f]) => f.default !== undefined)
      .map(([key, field]) => [key, field.default!]),
  );
}
export function validateOptions(
  kind: KindSchema | undefined,
  values: Record<string, unknown>,
): string {
  for (const [key, f] of fieldDefinitions(kind)) {
    const value = values[key],
      label = f.title || key;
    if (value === undefined || value === "") {
      if (f.required) return `请填写${label}`;
      continue;
    }
    if (f.enum && !f.enum.includes(value as string | number))
      return `${label}不在允许的选项中`;
    if (f.type === "object") {
      try {
        const object = typeof value === "string" ? JSON.parse(value) : value;
        if (
          !object ||
          typeof object !== "object" ||
          Array.isArray(object) ||
          Object.keys(object).length > (f.maxProperties || 32)
        )
          return `${label}需要有效的键值对象`;
        if (
          Object.entries(object).some(
            ([k, v]) =>
              k.length > 256 ||
              !["string", "number", "boolean"].includes(typeof v) ||
              String(v).length > 1024,
          )
        )
          return `${label}的键值格式无效`;
      } catch {
        return `${label}需要有效的 JSON 对象`;
      }
    }
    if (["number", "integer"].includes(f.type || "")) {
      if (
        typeof value !== "number" ||
        !Number.isFinite(value) ||
        (f.type === "integer" && !Number.isInteger(value))
      )
        return `${label}格式无效`;
      if (
        (f.minimum !== undefined && value < f.minimum) ||
        (f.maximum !== undefined && value > f.maximum)
      )
        return `${label}超出范围`;
    }
    if (typeof value === "string" && value.length > (f.maxLength || 2048))
      return `${label}过长`;
  }
  return "";
}
// 校验完成后再转换对象型 JSON，并去掉空字段；不偷偷把非法输入变成默认有效值。
export function normalizeOptions(
  kind: KindSchema | undefined,
  values: Record<string, unknown>,
): Record<string, unknown> {
  const error = validateOptions(kind, values);
  if (error) throw new Error(error);
  const result = { ...values };
  for (const key of Object.keys(result))
    if (result[key] === "" || result[key] === undefined) delete result[key];
  for (const [key, f] of fieldDefinitions(kind))
    if (f.type === "object" && typeof result[key] === "string")
      result[key] = JSON.parse(result[key] as string);
  return result;
}
// 分段构造字节串，避免大文件一次展开为函数参数导致调用栈/参数数量溢出。
export function encodeImport(data: Uint8Array): string {
  const chunks: string[] = [];
  for (let i = 0; i < data.length; i += 8192)
    chunks.push(String.fromCharCode(...data.subarray(i, i + 8192)));
  return btoa(chunks.join(""));
}
