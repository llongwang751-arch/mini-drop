import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Cpu, Database, ArrowRight } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import { api, number, bytes } from "../api";
import { pollingInterval } from "../query-policy";
import type { Overview as OverviewData, Agent, List } from "../types";
import {
  PageHeading,
  NewTaskLink,
  SectionHeading,
  TaskTable,
  Badge,
  ErrorPanel,
  Skeleton,
} from "../components/ui";
import TrendChart from "../components/TrendChart";

export default function Overview() {
  const query = useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => api<OverviewData>("/overview", { signal }),
    refetchInterval: (query) => pollingInterval(query, 10000),
  });
  const agents = useQuery({
    queryKey: ["agents", "overview"],
    queryFn: ({ signal }) =>
      api<List<Agent>>("/agents?page_size=4", { signal }),
    refetchInterval: (query) => pollingInterval(query, 10000),
  });
  const d = query.data;

  return (
    <>
      <PageHeading
        eyebrow="工作空间 / 总览"
        title="看见性能的每一面"
        description="从最近的诊断出发，追踪现场，定位每一份开销。"
        action={<NewTaskLink />}
      />
      {query.error ? (
        <ErrorPanel error={query.error} retry={() => query.refetch()} />
      ) : !d ? (
        <Skeleton rows={7} />
      ) : (
        <>
          <dl className="ov-metrics" aria-label="工作空间统计">
            {[
              {
                label: "诊断任务",
                value: number(d.tasks_total),
                suffix: "次",
                note: "累计创建",
                tone: "",
              },
              {
                label: "在线节点",
                value: number(d.agents_online),
                suffix: `/ ${d.agents_total}`,
                note: "实时心跳连接",
                tone: "green",
              },
              {
                label: "进行中",
                value: number(d.tasks_running),
                suffix: "次",
                note: "采集与分析中的任务",
                tone: "active",
              },
              {
                label: "诊断成功率",
                value: d.tasks_total ? d.success_rate.toFixed(1) : "—",
                suffix: d.tasks_total ? "%" : "",
                note: `${number(d.tasks_success)} 次分析已完成`,
                tone: "",
              },
            ].map((m) => (
              <div className={`ov-metric ${m.tone}`} key={m.label}>
                <dt className="ov-metric-label">{m.label}</dt>
                <dd className="ov-metric-value">
                  {m.value}
                  <span>{m.suffix}</span>
                </dd>
                <dd className="ov-metric-note">{m.note}</dd>
              </div>
            ))}
          </dl>
          <div className="ov-workspace">
            <div className="ov-main">
              <section className="panel ov-recents">
                <SectionHeading
                  title="最近诊断"
                  note="从采集现场到分析结果"
                  to="/tasks"
                />
                <TaskTable tasks={d.recent_tasks || []} compact />
              </section>
              <section className="panel ov-activity">
                <SectionHeading
                  title="诊断活动"
                  note="近 24 小时 · 每小时创建量"
                />
                <TrendChart data={d.trend} />
              </section>
            </div>
            <aside className="ov-aside" aria-label="节点与诊断队列">
              <section className="panel ov-nodes">
                <SectionHeading
                  title="节点健康"
                  note={`${number(d.agents_online)} / ${number(d.agents_total)} 在线`}
                  to="/agents"
                />
                {agents.error ? (
                  <ErrorPanel
                    error={agents.error}
                    retry={() => agents.refetch()}
                  />
                ) : !agents.data ? (
                  <Skeleton rows={2} />
                ) : agents.data.items?.length ? (
                  <div className="ov-node-list">
                    {agents.data.items.map((a) => (
                      <Link
                        key={a.id}
                        to={`/agents/${a.id}`}
                        className="ov-node-row"
                      >
                        <div className="ov-node-heading">
                          <span className="ov-node-icon">
                            <Cpu size={21} />
                          </span>
                          <div className="ov-node-name">
                            <strong>{a.hostname}</strong>
                            <small className="mono">{a.ip || a.id}</small>
                          </div>
                          <Badge status={a.status} />
                          <ArrowUpRight size={15} />
                        </div>
                        <dl className="ov-node-resources">
                          <div>
                            <dt>CPU</dt>
                            <dd className="mono">
                              {a.cpu_percent.toFixed(1)}%
                            </dd>
                          </div>
                          <div>
                            <dt>内存</dt>
                            <dd className="mono">
                              {a.memory_percent.toFixed(1)}%
                            </dd>
                          </div>
                        </dl>
                      </Link>
                    ))}
                  </div>
                ) : (
                  <div className="quiet-empty">
                    等待 Agent 接入，
                    <Link to="/guide">
                      查看接入指南 <ArrowRight size={13} />
                    </Link>
                  </div>
                )}
              </section>
              <section className="panel ov-queue">
                <SectionHeading title="诊断队列" note="当前任务分布" />
                <ol className="ov-queue-list">
                  {[
                    {
                      num: "01",
                      name: "等待采集",
                      note: "等待下发到节点",
                      count:
                        (d.collection_counts.CREATED || 0) +
                        (d.collection_counts.QUEUED || 0) +
                        (d.collection_counts.DELIVERED || 0),
                    },
                    {
                      num: "02",
                      name: "执行采集",
                      note: "节点采样与上传",
                      count:
                        (d.collection_counts.RUNNING || 0) +
                        (d.collection_counts.UPLOADING || 0),
                    },
                    {
                      num: "03",
                      name: "异步分析",
                      note: "调用栈聚合与分析",
                      count: Math.max(
                        0,
                        (d.collection_counts.COLLECTED || 0) -
                          d.tasks_success -
                          (d.analysis_counts.FAILED || 0) -
                          (d.analysis_counts.CANCELED || 0),
                      ),
                    },
                    {
                      num: "04",
                      name: "结果就绪",
                      note: "分析完成并已归档",
                      count: d.tasks_success,
                    },
                  ].map((stage) => (
                    <li className="ov-queue-stage" key={stage.num}>
                      <span className="ov-queue-index mono">{stage.num}</span>
                      <div className="ov-queue-label">
                        <strong>{stage.name}</strong>
                        <small>{stage.note}</small>
                      </div>
                      <span className="ov-queue-count mono">
                        {number(stage.count)}
                      </span>
                    </li>
                  ))}
                </ol>
              </section>
              <section className="ov-storage" aria-labelledby="storage-title">
                <Database size={21} />
                <div>
                  <h2 id="storage-title">制品存储</h2>
                  <p>原始采样与分析结果</p>
                </div>
                <strong className="mono">{bytes(d.storage_bytes || 0)}</strong>
              </section>
            </aside>
          </div>
        </>
      )}
    </>
  );
}
