import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import PlannerBlock from "./PlannerBlock";

afterEach(cleanup);

describe("PlannerBlock", () => {
  it("merges cross-round paraphrases while keeping the latest state and unique causes", () => {
    render(
      <PlannerBlock
        classification="CPU 性能问题"
        hypotheses={[
          {
            hypothesis_id: "hyp-hotspot-r1",
            round_index: 1,
            statement: "目标 Python 进程可能存在用户态 CPU 热点函数或 GIL 竞争",
            status: "OPEN",
            expected_observations: ["火焰图出现稳定热点"],
          },
          {
            hypothesis_id: "hyp-hotspot-r2",
            round_index: 2,
            statement: "Python 用户态 CPU 热点函数与 GIL contention 可能导致 CPU 持续升高",
            status: "SUPPORTED",
            source: "MODEL_REPLAN",
            falsification_criteria: ["调用栈没有热点且 GIL 等待不显著"],
          },
          {
            hypothesis_id: "hyp-io-r2",
            round_index: 2,
            statement: "磁盘 I/O 等待导致请求延迟",
            status: "OPEN",
          },
        ]}
      />,
    );

    expect(screen.getByText("Python 用户态 CPU 热点函数与 GIL contention 可能导致 CPU 持续升高")).toBeInTheDocument();
    expect(screen.queryByText("目标 Python 进程可能存在用户态 CPU 热点函数或 GIL 竞争")).not.toBeInTheDocument();
    expect(screen.getByText("跨轮合并：第 1、2 轮")).toBeInTheDocument();
    expect(screen.getByText("有证据支持")).toBeInTheDocument();
    expect(screen.getByText("火焰图出现稳定热点")).toBeInTheDocument();
    expect(screen.getByText("调用栈没有热点且 GIL 等待不显著")).toBeInTheDocument();
    expect(screen.getByText("磁盘 I/O 等待导致请求延迟")).toBeInTheDocument();
  });
});
