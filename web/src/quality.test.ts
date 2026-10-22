import { describe, it, expect } from "vitest";
import { filterQuality, metricText } from "./quality";
import type { QualityRecord } from "./quality";
describe("质量指标展示边界", () => {
  it("保留未知、零值和比例的区别", () => {
    expect(metricText(null, "ratio")).toBe("未验收");
    expect(metricText(0, "ratio")).toBe("0.0%");
    expect(metricText(0.75, "ratio")).toBe("75.0%");
    expect(metricText(1.25, "multiple")).toBe("1.250×");
  });
  it("按套件筛选，失败与未完成都不丢弃", () => {
    // 这里只是导入的合成列表夹具，不是接口或数据库运行记录。
    const rows = [
      { run: { suite: "suite-one", category: "system", status: "failed" } },
      { run: { suite: "suite-one", category: "system", status: "incomplete" } },
      { run: { suite: "suite-two", category: "agent", status: "passed" } },
    ] as QualityRecord[];
    expect(filterQuality(rows, "system", "suite-one", "")).toHaveLength(2);
    expect(filterQuality(rows, "", "", "failed")).toHaveLength(1);
  });
});
