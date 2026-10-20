/**
 * 检查结构化目标参数，尤其区分容器内 PID 与宿主机 PID；这里不解析真实容器或进程。
 */
import { expect, it } from "vitest";
import { emptyTarget, targetPayload } from "./TargetFields";
it("separates container-local PID 1 from an unresolved host PID", () => {
  const result = targetPayload({
    ...emptyTarget(),
    type: "container",
    container_id: "a".repeat(64),
    pid: "1",
  });
  expect(result.pid).toBe(0);
  expect(result.target.pid).toBe(0);
  expect(result.target).toHaveProperty("container_pid", 1);
});
it("keeps process identity pinned and does not require a PID for host resources", () => {
  expect(
    targetPayload({
      ...emptyTarget(),
      pid: "42",
      start_time_ticks: "123456789",
    }).target,
  ).toEqual({ type: "process", pid: 42, start_time_ticks: 123456789 });
  expect(targetPayload({ ...emptyTarget(), type: "host", pid: "42" }).pid).toBe(
    0,
  );
});
