/**
 * 模拟本地存储可用/不可用的浏览器偏好，确认导航不会因持久化失败而失效。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  readCompactNavigation,
  saveCompactNavigation,
} from "./navigation-preference";

afterEach(() => vi.unstubAllGlobals());

describe("navigation preferences", () => {
  it("persists an explicit choice and ignores unknown stored values", () => {
    const values = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    });
    expect(readCompactNavigation()).toBe(false);
    saveCompactNavigation(true);
    expect(readCompactNavigation()).toBe(true);
    saveCompactNavigation(false);
    expect(readCompactNavigation()).toBe(false);
    values.set("drop-navigation-compact-v1", "unexpected");
    expect(readCompactNavigation()).toBe(false);
  });

  it("keeps navigation usable when browser storage is denied", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("Storage denied");
      },
      setItem: () => {
        throw new Error("Storage denied");
      },
    });
    expect(readCompactNavigation()).toBe(false);
    expect(() => saveCompactNavigation(true)).not.toThrow();
  });
});
