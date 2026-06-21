import { ArrowClockwise, CheckCircle, Heartbeat, Pulse, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef } from "react";
import { MetricCard } from "../components/MetricCard";
import { ContinuousPanel } from "../features/tasks/ContinuousPanel";
import { TaskDetail } from "../features/tasks/TaskDetail";
import { TaskTable } from "../features/tasks/TaskTable";
import { NaturalLanguageCommand } from "../features/natural-language/NaturalLanguageCommand";
import { useDashboardStore } from "../store/dashboardStore";

export function OverviewPage() {
  const { agents, tasks, selectedTask, refresh, selectTask } = useDashboardStore();
  const detailRef = useRef<HTMLDivElement>(null);
  const metrics = useMemo(
    () => ({
      agents: agents.filter((agent) => agent.online).length,
      running: tasks.filter((task) => ["RUNNING", "UPLOADING"].includes(task.status)).length,
      done: tasks.filter((task) => task.status === "DONE").length,
      failed: tasks.filter((task) => task.status === "FAILED").length,
    }),
    [agents, tasks],
  );

  useEffect(() => {
    if (selectedTask) {
      window.setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
    }
  }, [selectedTask?.id]);

  return (
    <>
      <div className="heading">
        <div>
          <p className="eyebrow">PERFORMANCE WORKSPACE</p>
          <h1>性能任务</h1>
          <span>下发真实采集任务，跟踪状态，定位内核与进程热点。</span>
        </div>
        <button className="refresh" onClick={() => void refresh()}>
          <ArrowClockwise size={17} />
          每 2 秒自动刷新
        </button>
      </div>
      <section className="metrics">
        <MetricCard label="在线 Agent" value={metrics.agents} note="可接收任务" icon={Heartbeat} />
        <MetricCard label="执行中" value={metrics.running} note="采集或分析中" icon={Pulse} />
        <MetricCard label="已完成" value={metrics.done} note="结果可查看" icon={CheckCircle} />
        <MetricCard label="失败" value={metrics.failed} note="需要排查" icon={WarningCircle} />
      </section>
      <NaturalLanguageCommand />
      <section className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">RECENT PROFILES</p>
            <h2>最近任务</h2>
          </div>
          <span className="panel-note">点击任务查看完整分析</span>
        </div>
        <TaskTable tasks={tasks} selectedId={selectedTask?.id} onSelect={(id) => void selectTask(id)} />
      </section>
      <div ref={detailRef}>
        <TaskDetail task={selectedTask} />
      </div>
      <ContinuousPanel agents={agents} />
    </>
  );
}
