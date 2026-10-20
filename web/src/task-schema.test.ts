/**
 * 对任务类型参数 schema 使用合成输入验证范围、枚举和对象约束，不拼接或执行命令行。
 */
import { describe, expect, it } from "vitest";
import {
  fieldDefinitions,
  initialOptions,
  validateOptions,
  encodeImport,
  normalizeOptions,
} from "./task-schema";

describe("task kind schema driven forms", () => {
  const kind = {
    parameter_schema: {
      type: "object",
      required: ["event"],
      properties: {
        event: { type: "string", enum: ["cpu", "alloc"], default: "cpu" },
        sample_bytes: {
          type: "integer",
          minimum: 1024,
          maximum: 65536,
          default: 4096,
        },
      },
    },
  };
  it("uses the authoritative schema and retains false/zero defaults", () => {
    expect(initialOptions(kind)).toEqual({ event: "cpu", sample_bytes: 4096 });
    expect(
      fieldDefinitions({
        parameters: { native: { type: "boolean", default: false } },
      }),
    ).toHaveLength(1);
  });
  it("rejects out of range, fractional and unregistered enum inputs", () => {
    expect(
      validateOptions(kind, { event: "wall", sample_bytes: 4096 }),
    ).toBeTruthy();
    expect(
      validateOptions(kind, { event: "cpu", sample_bytes: 1024.5 }),
    ).toBeTruthy();
    expect(
      validateOptions(kind, { event: "cpu", sample_bytes: 70000 }),
    ).toBeTruthy();
    expect(validateOptions(kind, initialOptions(kind))).toBe("");
  });
  it("encodes arbitrary binary input without corrupting non UTF-8 profile bytes", () => {
    expect(encodeImport(new Uint8Array([0, 255, 128, 1]))).toBe("AP+AAQ==");
  });
});

it("normalizes bounded label objects without discarding zero or false", () => {
  const kind = {
    parameter_schema: {
      properties: {
        options: {
          type: "object",
          properties: {
            label_filter: { type: "object" },
            optional: { type: "string" },
            zero: { type: "integer" },
            native: { type: "boolean" },
          },
        },
      },
    },
  };
  expect(
    normalizeOptions(kind, {
      label_filter: '{"service":"checkout"}',
      optional: "",
      zero: 0,
      native: false,
    }),
  ).toEqual({ label_filter: { service: "checkout" }, zero: 0, native: false });
  expect(() => normalizeOptions(kind, { label_filter: '{"a":[1]}' })).toThrow();
});
