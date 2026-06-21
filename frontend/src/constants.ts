import type { Collector, StatusValue } from "./types";

export const statusText: Record<StatusValue, string> = {
  PENDING: "等待执行",
  RUNNING: "正在采集",
  UPLOADING: "正在分析",
  DONE: "已完成",
  FAILED: "失败",
};

export const collectorText: Record<Collector, string> = {
  perf: "CPU / perf",
  ebpf: "eBPF 内核探针",
  pyspy: "Python 调用栈",
};

export const reasonText: Record<string, string> = {
  "task accepted": "任务已创建，等待 Agent 领取",
  "claimed by agent": "Agent 已领取任务",
  "collector finished; uploading": "采集完成，正在上传分析",
  "analyzer produced visualizations": "分析器已生成可视化结果",
  "agent connected or recovered": "Agent 已连接或恢复",
  "no heartbeat for 30s": "超过 30 秒未收到心跳",
};

export const formatTime = (timestamp: number) =>
  new Date(timestamp * 1000).toLocaleTimeString("zh-CN", { hour12: false });

export const formatPreciseTime = (timestamp: number) => {
  const date = new Date(timestamp * 1000);
  const base = date.toLocaleTimeString("zh-CN", { hour12: false });
  return `${base}.${String(date.getMilliseconds()).padStart(3, "0")}`;
};
