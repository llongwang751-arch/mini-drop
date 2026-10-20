/**
 * 用可控 Query 状态验证轮次退避，区分单次请求内部重试与下一轮轮询间隔。
 */
import { describe, expect, it } from "vitest";
import { APIError } from "./api";
import { pollingInterval, retryQuery } from "./query-policy";

const query = () => ({
  state: { status: "pending", errorUpdateCount: 0, error: null as unknown },
});

describe("query lifecycle and outage polling policy", () => {
  it("backs off across consecutive failed polling cycles without double-counting observer updates", () => {
    const current = query();
    expect(pollingInterval(current, 5000)).toBe(5000);
    current.state.status = "error";
    current.state.errorUpdateCount++;
    expect(pollingInterval(current, 5000)).toBe(10000);
    expect(pollingInterval(current, 5000)).toBe(10000);
    current.state.status = "pending";
    expect(pollingInterval(current, 5000)).toBe(10000);
    current.state.status = "error";
    current.state.errorUpdateCount++;
    expect(pollingInterval(current, 5000)).toBe(20000);
    current.state.errorUpdateCount++;
    expect(pollingInterval(current, 5000)).toBe(40000);
    current.state.errorUpdateCount++;
    expect(pollingInterval(current, 5000)).toBe(60000);
    current.state.errorUpdateCount = 100000;
    expect(pollingInterval(current, 5000)).toBe(60000);
  });
  it("resets after success and keeps independent queries independent", () => {
    const failed = query(),
      healthy = query();
    failed.state.status = "error";
    failed.state.errorUpdateCount = 3;
    expect(pollingInterval(failed, 10000)).toBe(20000);
    expect(pollingInterval(healthy, 10000)).toBe(10000);
    failed.state.status = "success";
    expect(pollingInterval(failed, 10000)).toBe(10000);
    failed.state.status = "error";
    failed.state.errorUpdateCount++;
    expect(pollingInterval(failed, 10000)).toBe(20000);
  });
  it("stops terminal tasks and permanent authorization/resource errors", () => {
    const current = query();
    expect(pollingInterval(current, 5000, true)).toBe(false);
    current.state.status = "error";
    for (const status of [400, 401, 403, 404, 409, 410, 422]) {
      current.state.error = new APIError(
        "Imported failure fixture",
        "FIXTURE",
        "fixture",
        status,
      );
      expect(pollingInterval(current, 5000)).toBe(false);
      expect(retryQuery(0, current.state.error)).toBe(false);
    }
  });
  it("allows one transient retry with bounded polling, never retries navigation aborts", () => {
    for (const status of [0, 408, 429, 500, 503]) {
      const error = new APIError(
        "Imported transient fixture",
        "FIXTURE",
        "fixture",
        status,
      );
      expect(retryQuery(0, error)).toBe(true);
      expect(retryQuery(1, error)).toBe(false);
    }
    expect(retryQuery(0, new DOMException("Navigation", "AbortError"))).toBe(
      false,
    );
  });
});
